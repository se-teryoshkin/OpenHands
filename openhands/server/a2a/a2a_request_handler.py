import asyncio
import os
import base64
from collections.abc import AsyncGenerator
from copy import copy
from datetime import datetime, timezone, UTC
from typing import Optional
from typing import Any, Dict
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from a2a.server.events import Event as A2AEvent
from a2a.types import (
    Artifact, MessageSendParams, Role, Task, TaskIdParams, TaskQueryParams, TaskState,
    TaskStatus, TextPart, FilePart, FileWithBytes, TaskPushNotificationConfig, Message as A2AMessage,
    TaskNotFoundError, UnsupportedOperationError, InvalidParamsError, InternalError,
)
from a2a.utils.errors import ServerError

from openhands.core.exceptions import AgentRuntimeUnavailableError
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import ActionType
from openhands.core.schema.agent import AgentState
from openhands.events import EventStreamSubscriber
from openhands.events.action import (
    NullAction, SystemMessageAction, RecallAction, Action, ChangeAgentStateAction, MessageAction
)
from openhands.events.event import Event, EventSource
from openhands.events.event_store import EventStore

from openhands.events.observation import NullObservation
from openhands.events.observation.agent import AgentStateChangedObservation, RecallObservation
from openhands.events.serialization import event_to_dict
from openhands.runtime import Runtime
from openhands.integrations.provider import ProviderHandler
from openhands.server.services.conversation_service import create_new_conversation
from openhands.server.session.agent_session import AgentSession
from openhands.server.session.conversation_init_data import ConversationInitData
from openhands.server.shared import (
    SecretsStoreImpl,
    SettingsStoreImpl,
    config,
    server_config,
    conversation_manager,
    file_store, ConversationStoreImpl
)
from openhands.server.types import AppMode
from openhands.storage.conversation.conversation_store import ConversationStore
from openhands.storage.data_models.conversation_metadata import ConversationTrigger
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.locations import get_task_filename, get_tasks_folder
from openhands.utils.async_utils import call_async_from_sync

TASK_TERMINAL_STATES = (TaskState.failed, TaskState.canceled, TaskState.completed, TaskState.rejected)
METADATA_NAME_PREFIX = "openhands"
AUTO_CONTINUE_RESPONSE = (
    'Please continue on whatever approach you think is suitable.\n'
    'If you think you have solved the task, please finish the interaction.\n'
    'IMPORTANT: YOU SHOULD NOT ASK FOR HUMAN RESPONSE UNTIL USER CONTACT YOU HIMSELF.\n'
)
A2A_SUBSCRIBE_ID = 'a2a_task_{task_id}'


class TaskSave(BaseModel):
    context_id: str
    id: str
    status: TaskStatus
    min_event_id: int | None = None
    max_event_id: int | None = None
    metadata: dict[str, Any] | None = None
    artifacts: list[Artifact] | None = None


class A2AOHTaskWrapper:

    def __init__(
            self,
            task_id: str,
            context_id: str,
            metadata: dict[str, Any] | None = None,
    ):

        if metadata is None:
            metadata: dict[str, Any] = dict()

        self.task_id = task_id
        self.status = TaskStatus(state=TaskState.submitted)
        self.history: list[A2AMessage] = list()
        # TODO: Необходимо придумать способ хранить zip-архивы тасок
        #  на диске, чтобы в рамках одного контекста можно было отдавать разные
        #  снапшоты рабочей директории, а также сохранялось состояние при перезапуске OH.
        self.artifacts: list[Artifact] = list()
        self.min_event_id = -1
        self.max_event_id = -1
        self.events: dict[int, Event] = dict()
        self.is_finished = False
        self.subscribed = False
        self._unsubscribe_event = asyncio.Event()
        self.agent_session: Optional[AgentSession] = None

        auto_continue = metadata.get(f"{METADATA_NAME_PREFIX}/auto-continue", False)
        metadata[f"{METADATA_NAME_PREFIX}/auto-continue"] = auto_continue

        self.auto_continue = auto_continue
        self.metadata = metadata
        self.context_id = context_id

    def __repr__(self) -> str:
        return (f"Task(id={self.task_id}, status={self.status}, "
                f"history_length={len(self.history)}, metadata={self.metadata})")


    def update_status(
            self,
            state: TaskState,

            # TODO: Возможно стоит удалить эти messages? Всё равно из истории подтягивать будем при to_response
            text: str | None = None,
            message: A2AMessage | None = None,
    ) -> None:

        # TODO: Добавить прокидывание сообщений клиенту
        #  TaskStatusUpdateEventObject - https://a2a-protocol.org/latest/specification/#722-taskstatusupdateevent-object

        if self.status.state in TASK_TERMINAL_STATES:
            return

        self.status.state = state

        if message is not None:
            self.status.message = message
        elif text is not None:
            self.status.message = A2AMessage(
                context_id=self.context_id,
                message_id=uuid4().hex,
                parts=[TextPart(text=text)],
                role=Role.agent,
                task_id=self.task_id,
            )
        else:
            self.status.message = None

        self.status.timestamp = datetime.now(UTC).isoformat()

        if state in TASK_TERMINAL_STATES:
            self.is_finished = True
        save_task(self, None)

    def to_response(self, history_length: int | None = None, show_all_events: bool | None = None) -> Task:
        if show_all_events is None:
            show_all_events = False

        if self.status.state in (TaskState.failed, TaskState.input_required):

            status_message = None
            for message in reversed(self.history):

                metadata = message.metadata
                if metadata is not None:
                    event_id = metadata.get(f"{METADATA_NAME_PREFIX}/event-id", None)

                    if event_id in self.events:
                        event = self.events[event_id]
                        if isinstance(event, Action) and not isinstance(
                                event, (SystemMessageAction, RecallAction, NullAction)
                        ):
                            status_message = message
                            break

            if status_message is not None:
                self.status.message = status_message
            else:
                self.status.message = self.history[-1]

        if show_all_events:
            if history_length is not None:
                if history_length > 0:
                    history = self.history[-history_length:]
                else:
                    history = list()
            else:
                history = self.history
        else:
            history = list()

            if history_length is None or history_length > 0:
                for message in reversed(self.history):

                    metadata = message.metadata
                    if metadata is not None:
                        event = self.events[metadata.get(f"{METADATA_NAME_PREFIX}/event-id")]
                        if (isinstance(event, Action)
                                and not isinstance(event, (
                                        # System events
                                        NullAction,
                                        NullObservation,
                                        AgentStateChangedObservation,
                                        SystemMessageAction,
                                        RecallAction,
                                        RecallObservation,
                                        ChangeAgentStateAction,
                                ))):
                            history.append(message)
                            if history_length is not None and len(history) >= history_length:
                                break
                history = history[::-1]

        artifacts = None
        if self.status.state is TaskState.completed and len(self.artifacts) > 0:
            artifacts = self.artifacts

        return Task(
            id=self.task_id,
            context_id=self.context_id,
            status=self.status,
            history=history,
            metadata=copy(self.metadata),
            artifacts=artifacts
        )

    def on_event(self, event: Event) -> None:

        task_id = self.task_id
        self.events[event.id] = event

        if self.min_event_id < 0:
            self.min_event_id = event.id

        if event.id > self.max_event_id:
            self.max_event_id = event.id

        agent_state = getattr(event, "agent_state", "")

        # FIXME: Catch user messages and replace with appropriate message_id
        #  Otherwise it will duplicate user messages
        message = A2AMessage(
            role=Role.user if event.source == EventSource.USER else Role.agent,

            # TODO: Добавить проверку на уникальность message_id
            message_id=(
                getattr(event, "a2a_metadata", dict()).get("message_id", f"{task_id}-{event.id}")
            ),

            context_id=self.context_id,

            # TODO: Add multiple parts
            parts=[TextPart(text=event.message if event.message is not None else "")],

            task_id=task_id,
            metadata={
                f"{METADATA_NAME_PREFIX}/event-source": event.source,
                f"{METADATA_NAME_PREFIX}/event-id": event.id,
                f"{METADATA_NAME_PREFIX}/agent-state": agent_state,
                f"{METADATA_NAME_PREFIX}/event-type": type(event).__name__,
                f"{METADATA_NAME_PREFIX}/event-timestamp": event.timestamp,
            }
        )

        self.history.append(message)

        match agent_state:
            case AgentState.FINISHED:
                self.update_status(TaskState.completed)
            case AgentState.STOPPED:
                self.update_status(TaskState.canceled)
            case AgentState.AWAITING_USER_INPUT | AgentState.AWAITING_USER_CONFIRMATION:

                # FIXME: This is hacky. On OpenHands Session startup events with id=2 and id=4
                #  are Environment events with AgentState.AWAITING_USER_INPUT, following them
                #  the initial user message is passed to the agent.
                if event.id > 4:
                    if (
                            self.auto_continue
                            and agent_state == AgentState.AWAITING_USER_INPUT
                            and isinstance(event, AgentStateChangedObservation)
                    ):
                        self.agent_session.event_stream.add_event(
                            MessageAction(content=AUTO_CONTINUE_RESPONSE),
                            EventSource.USER,
                        )
                    else:
                        self.update_status(TaskState.input_required)
            case AgentState.RUNNING | AgentState.LOADING | AgentState.RATE_LIMITED | AgentState.USER_CONFIRMED:
                self.update_status(TaskState.working)
            case AgentState.ERROR:
                self.update_status(TaskState.failed)
            case _:
                pass

        if self.is_finished:

            # Finish processing, like close stream, create artifacts, etc.

            # Temporary disabled
            ENABLE_ARTIFACTS = False
            if self.status.state == TaskState.completed and ENABLE_ARTIFACTS:
                file = self.zip_current_workspace()
                if file is not None:
                    self.artifacts.append(Artifact(
                        artifact_id=uuid4().hex,
                        parts=[FilePart(file=file)]
                    ))
            save_task(self, None)

            # Прямой вызов event_stream.unsubscribe невозможен, так как при его вызове
            # происходит thread_pool.shutdown() из того же потока, который находится в пуле,
            # что приводит к исключению. Один из аккуратных выходов из этой ситуации - создать флаг `asyncio.Event`,
            # и когда нужно выполнить unsubscribe, мы устанавливаем этот флаг. При subscribe мы в фон сразу же
            # создаём вызов `unsubscribe`, в котором выполняется ожидание флага.
            self._unsubscribe_event.set()

    async def subscribe(self):

        if self.subscribed:
            return

        self.agent_session.event_stream.subscribe(
            EventStreamSubscriber.SERVER,
            self.on_event,
            A2A_SUBSCRIBE_ID.format(task_id=self.task_id),
        )

        self.subscribed = True
        self._unsubscribe_event = asyncio.Event()

        asyncio.create_task(self.unsubscribe())

    async def unsubscribe(self):

        if not self.subscribed:
            return

        await self._unsubscribe_event.wait()

        self.agent_session.event_stream.unsubscribe(
            EventStreamSubscriber.SERVER,
            A2A_SUBSCRIBE_ID.format(task_id=self.task_id)
        )

        self.subscribed = False

    def set_agent_session(self) -> Optional[AgentSession]:
        agent_session = conversation_manager.get_agent_session(self.context_id)

        if agent_session is None:
            logger.error(f"No agent_session for {self.context_id} after waiting")

        self.agent_session = agent_session
        return agent_session

    def zip_current_workspace(self) -> FileWithBytes | None:
        try:
            logger.debug('Zipping workspace')
            runtime: Runtime = self.agent_session.runtime
            path = runtime.config.workspace_mount_path_in_sandbox
            try:
                zip_file_path = runtime.copy_from(path)
            except AgentRuntimeUnavailableError as e:
                logger.error(f'Error zipping workspace: {e}')
                return None

            zip_b64 = base64.b64encode(zip_file_path.read_bytes())
            file = FileWithBytes(
                name='workspace.zip',
                mime_type='application/zip',
                bytes=zip_b64
            )
            os.unlink(zip_file_path)
            return file

        except Exception as e:
            logger.error(f'Error zipping workspace: {e}')

    def get_data(self) -> TaskSave:
        if len(self.events) > 1:
            min_event_id = self.min_event_id
            max_event_id = self.max_event_id
        else:
            min_event_id = None
            max_event_id = None

        return TaskSave(
            context_id=self.context_id,
            id=self.task_id,
            status=self.status,
            min_event_id = min_event_id,
            max_event_id = max_event_id,
            metadata=copy(self.metadata),
            artifacts=copy(self.artifacts),
        )


def load_tasks_json(context_id: str, user_id: str | None) -> dict[str, A2AOHTaskWrapper]:
    path = get_tasks_folder(context_id, user_id)
    try:
        files = file_store.list(path)
        tasks: dict[str, A2AOHTaskWrapper] = dict()
        for filename in files:
            content = file_store.read(filename)
            try:
                task_save = TaskSave.model_validate_json(content)
            except ValidationError:
                continue

            task = A2AOHTaskWrapper(task_id=task_save.id, context_id=context_id, metadata=copy(task_save.metadata))
            task.min_event_id = task_save.min_event_id
            task.max_event_id = task_save.max_event_id
            task.artifacts = copy(task_save.artifacts)
            task.status = task_save.status

            events: dict[int, Event] = dict()
            history: list[A2AMessage] = list()
            if task_save.min_event_id is not None and task_save.max_event_id is not None:
                event_store = EventStore(
                    sid=context_id,
                    file_store=file_store,
                    user_id=user_id,
                )

                loaded_events = list(
                    event_store.search_events(
                        start_id=task_save.min_event_id,
                        end_id=task_save.max_event_id
                    )
                )

                for event in loaded_events:
                    message = A2AMessage(
                        role=Role.user if event.source == EventSource.USER else Role.agent,
                        message_id=f"{task.task_id}-{event.id}",
                        context_id=context_id,
                        parts=[TextPart(text=event.message if event.message is not None else "")],
                        task_id=task.task_id,
                        metadata={
                            f"{METADATA_NAME_PREFIX}/event-source": event.source,
                            f"{METADATA_NAME_PREFIX}/event-id": event.id,
                            f"{METADATA_NAME_PREFIX}/agent-state": getattr(event, "agent_state", ""),
                            f"{METADATA_NAME_PREFIX}/event-type": type(event).__name__,
                            f"{METADATA_NAME_PREFIX}/event-timestamp": event.timestamp,
                        }
                    )
                    events[event.id] = event
                    history.append(message)
            task.events = events
            task.history = history
            if task.status.state == TaskState.working:
                # TODO: Сделать обработку ситуации, где загруженный task в состоянии working
                task.status.state = TaskState.unknown
            tasks[task.task_id] = task

    except Exception as e:
        logger.error(f"Error during tasks load for conversation {context_id}. Reason: {e}")
        return dict()

    return tasks


def load_conversations() -> dict[str, A2AOHTaskWrapper]:
    loaded_tasks: dict[str, A2AOHTaskWrapper] = dict()
    conversation_store: ConversationStore = call_async_from_sync(
        ConversationStoreImpl.get_instance,
        config=config,
        user_id=None
    )
    conversations = call_async_from_sync(conversation_store.search, limit=1000)
    conversations = [c.conversation_id for c in conversations.results]

    for context_id in conversations:
        loaded_tasks.update(load_tasks_json(context_id, None))

    return loaded_tasks


class A2aRequestHandler:

    # task_id -> task
    _tasks: dict[str, A2AOHTaskWrapper] = load_conversations()

    # context_id -> task
    _current_session_tasks: dict[str, A2AOHTaskWrapper] = dict()


    def _get_task_by_id(self, task_id: str) -> A2AOHTaskWrapper:
        task: A2AOHTaskWrapper | None = self._tasks.get(task_id, None)

        if task is None:
            raise ServerError(error=TaskNotFoundError())

        return task


    def check_if_no_tasks_running_for_context(self, task: A2AOHTaskWrapper, context_id: str) -> bool:
        current_task = self._current_session_tasks.get(context_id, None)

        if current_task is not None and current_task.status.state not in TASK_TERMINAL_STATES:
            task.update_status(
                TaskState.rejected, text="You cannot run multiple tasks simultaneously in the context"
            )
            return False

        self._current_session_tasks[context_id] = task
        return True


    async def on_cancel_task(self, params: TaskIdParams, context) -> Task | None:
        # TODO: Check if it will work in case of task is canceled while runtime is starting.
        task = self._get_task_by_id(params.id)
        asyncio.create_task(
            self.process_message(params, task)
        )
        task.update_status(TaskState.canceled)
        return task.to_response()


    async def on_message_send(
        self, params: MessageSendParams, context
    ) -> A2AMessage | Task:

        logger.debug(f"A2AMessageSendParams: {params}")

        # TODO: Add support for MessageSendConfiguration
        # TODO: Add reject in case of params.configuration.blocking is True
        task_id = params.message.task_id
        if task_id is None:
            task_id = uuid4().hex

        context_id = params.message.context_id

        if task_id not in self._tasks:

            # New task

            task = A2AOHTaskWrapper(task_id=task_id, metadata=copy(params.metadata), context_id=context_id)

            if context_id is None or conversation_manager.get_agent_session(task.context_id) is None:

                # No OH-conversation found, create new one

                if context_id is None:
                    context_id = uuid4().hex

                task.context_id = context_id
                try:
                    await self.create_new_conversation(params=params, context_id=context_id)
                except Exception as e:
                    logger.error(error_msg := f'Error creating new conversation: {e}')
                    # task.update_status(TaskState.failed, text=error_msg)
                    raise ServerError(error=InternalError(message=error_msg))

            # TODO: Add check if agent name stay the same in metadata for the same context, otherwise throw an exception.
            self._tasks[task.task_id] = task
        else:
            task = self._tasks[task_id]
            if task.status.state in TASK_TERMINAL_STATES:
                raise ServerError(error=InvalidParamsError(
                    message="The task already is in terminal state, you cannot interact it"
                ))

        check_status = self.check_if_no_tasks_running_for_context(task=task, context_id=context_id)

        if check_status:

            # Update state after the input-required next message
            if task.status.state == TaskState.input_required:
                task.update_status(TaskState.working)

            save_task(task, None)

            asyncio.create_task(
                self.process_message(params, task)
            )

        return task.to_response(
            history_length=(
                None
                if params.configuration is None
                else params.configuration.history_length
            )
         )

    async def on_message_send_stream(
        self, params: MessageSendParams
    ) -> AsyncGenerator[A2AEvent]:
        # TODO: Для реализации почти всё готово. Осталось только обернуть в асинхронный
        #  генератор объект A2AOHTaskWrapper и отправлять события события
        #  при task.status_update, task.on_event + обернуть task.history.
        raise ServerError(error=UnsupportedOperationError())

    async def on_set_task_push_notification_config(
        self, params: TaskPushNotificationConfig
    ) -> TaskPushNotificationConfig:
        raise ServerError(error=UnsupportedOperationError())

    async def on_get_task_push_notification_config(
        self, params: TaskIdParams
    ) -> TaskPushNotificationConfig:
        raise ServerError(error=UnsupportedOperationError())

    async def on_resubscribe_to_task(
        self, params: TaskIdParams
    ) -> AsyncGenerator[A2AEvent]:
        raise ServerError(error=UnsupportedOperationError())

    def should_add_push_info(self, params: MessageSendParams) -> bool:
        raise ServerError(error=UnsupportedOperationError())

    async def on_get_task(self, params: TaskQueryParams, context) -> Task | None:
        show_all_events = False
        if (metadata := params.metadata) is not None:
            show_all_events = metadata.get(f"{METADATA_NAME_PREFIX}/show-all-events", False)

        return self._get_task_by_id(params.id).to_response(
            history_length=params.history_length,
            show_all_events=show_all_events
        )

    # TODO: Add tasks/list
    async def on_list_task(self):
        raise ServerError(error=UnsupportedOperationError())

    def _convert_a2a_params_to_dict(self, params) -> Dict[str, Any]:

        match params:
            case MessageSendParams():
                # with stream
                msg = params.message
                event_dict = {
                    "action": ActionType.MESSAGE,
                    "args": {
                        "content": msg.parts[0].root.text,
                        "image_urls": [],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    "messageId": msg.message_id,
                    "source": EventSource.USER,
                }
            case _ if isinstance(params, (TaskQueryParams, TaskIdParams)):
                # with tasks/pushNotificationConfig/get tasks/resubscribe
                event_dict = {
                    "action": ActionType.CHANGE_AGENT_STATE,
                    "args": {
                        "agent_state": AgentState.STOPPED,
                    },
                    "source": EventSource.USER,
                }
            case _:
                raise ServerError(error=UnsupportedOperationError())

        return event_dict

    async def _conversation_init_data_set(
        self,
        params: MessageSendParams | TaskQueryParams | TaskIdParams,
        user_id: str | None = None,
    ):
        # TODO: Fix user_id?
        settings_store = await SettingsStoreImpl.get_instance(config, user_id=user_id)
        settings = await settings_store.load()

        # TODO: Fix user_id?
        secrets_store = await SecretsStoreImpl.get_instance(config, user_id=user_id)
        user_secrets: Secrets | None = await secrets_store.load()

        if not settings:
            raise ConnectionRefusedError(
                'Settings not found', {'msg_id': 'CONFIGURATION$SETTINGS_NOT_FOUND'}
            )

        session_init_args: dict = dict()
        if params.metadata is not None:
            if agent_name := params.metadata.get(f'{METADATA_NAME_PREFIX}/agent', None):
                session_init_args['agent'] = agent_name

        if settings:
            session_init_args = {**settings.__dict__, **session_init_args}

        if server_config.app_mode != AppMode.SAAS and user_secrets:
            git_provider_tokens = user_secrets.provider_tokens
            session_init_args['git_provider_tokens'] = git_provider_tokens

        if user_secrets:
            session_init_args['custom_secrets'] = user_secrets.custom_secrets

        conversation_init_data = ConversationInitData(**session_init_args)

        return conversation_init_data

    async def create_new_conversation(self,
                                      params: MessageSendParams | TaskQueryParams | TaskIdParams,
                                      context_id: str,
                                      user_id: str | None = None):

        data = await self._conversation_init_data_set(params=params, user_id=user_id)

        logger.info(f'Initializing_new_conversation:{data}')
        repository = data.selected_repository
        git_provider = data.git_provider

        if repository:
            await ProviderHandler(data.git_provider_tokens).verify_repo_provider(repository, git_provider)

        agent_loop_info = await create_new_conversation(
            user_id=user_id,
            git_provider_tokens=data.git_provider_tokens,
            custom_secrets=data.custom_secrets,
            selected_repository=repository,
            selected_branch=data.selected_branch,
            initial_user_msg=None,
            image_urls=list(),
            replay_json=data.replay_json,
            conversation_trigger=ConversationTrigger.SUGGESTED_TASK,
            conversation_instructions=data.conversation_instructions,
            conversation_title=f"A2A {context_id}",
            git_provider=git_provider,
            conversation_id=context_id,
            mcp_config=data.mcp_config,
        )

    async def process_message(
            self,
            params: MessageSendParams | TaskQueryParams | TaskIdParams,
            task: A2AOHTaskWrapper
    ) -> None:

        try:

            task.set_agent_session()
            await task.subscribe()

            # Wait for agent become ready
            await conversation_manager.get_agent_session(task.context_id).is_ready.wait()

            if task.status.state == TaskState.input_required:
                task.update_status(TaskState.working)
            await self.dispatch(params, task)
            logger.debug(f"Finished background task for message {params}")
        except Exception as e:
            logger.error(error_msg := f'Exception while processing message: {e}')
            task.update_status(TaskState.failed, text=error_msg)

    async def dispatch(self,
                       params: MessageSendParams | TaskQueryParams | TaskIdParams,
                       task: A2AOHTaskWrapper) -> None:

        try:

            a2a_metadata = {"message_id": params.message.message_id}

            if isinstance(params, MessageSendParams):
                event = MessageAction(content=params.message.parts[0].root.text, image_urls=[])
                event.a2a_metadata = a2a_metadata

                # TODO: Сделать отправку ивентов одинаковыми в обоих случаях
                await conversation_manager.send_event_to_conversation(sid=task.context_id, data=event_to_dict(event))

            elif isinstance(params, (TaskQueryParams, TaskIdParams)):
                event = ChangeAgentStateAction(agent_state=AgentState.STOPPED)
                event.a2a_metadata = a2a_metadata

                task.agent_session.event_stream.add_event(event=event, source=EventSource.USER)
        except Exception as e:
            logger.error(error_msg := f'Error adding message to conversation: {e}')
            task.update_status(TaskState.failed, text=error_msg)


def save_task(task: A2AOHTaskWrapper, user_id: str | None):
    task_json = task.get_data().model_dump_json(exclude_none=True).encode(encoding="utf-8")
    filename = get_task_filename(task.context_id, user_id, task.task_id)
    encoded = base64.b64encode(task_json)
    if len(encoded) > 1_000_000:
        logger.warning(
            f'Saving task JSON over 1MB: {len(encoded):,} bytes, filename: {filename}',
            extra={
                'user_id': user_id,
                'session_id': task.context_id,
                'size': len(task_json),
            },
        )
    file_store.write(filename, task_json)

