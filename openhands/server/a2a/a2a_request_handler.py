import asyncio
import os
import base64
from collections.abc import AsyncGenerator
from copy import copy
from datetime import datetime, timezone, UTC
from os.path import isfile, join
from typing import Any, Dict, Tuple
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from a2a.server.events import Event as A2AEvent
from a2a.types import (
    Artifact,
    Message as A2AMessage,
    MessageSendParams,
    Role,
    Task,
    TaskIdParams,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    UnsupportedOperationError,
    InvalidParamsError,
    TextPart,
    FilePart, FileWithBytes
)
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import SpanKind, trace_class, trace_function

from openhands.core.exceptions import AgentRuntimeUnavailableError
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import ActionType
from openhands.core.schema.agent import AgentState
from openhands.events.action import NullAction, SystemMessageAction, RecallAction, Action, ChangeAgentStateAction, \
    MessageAction
from openhands.events.event import Event, EventSource
from openhands.events.observation import NullObservation
from openhands.events.observation.agent import AgentStateChangedObservation, RecallObservation
from openhands.io import json
from openhands.runtime import Runtime
from openhands.server.session.conversation_init_data import ConversationInitData
from openhands.server.session.session import A2AWebSession
from openhands.server.shared import (
    SecretsStoreImpl,
    SettingsStoreImpl,
    config,
    file_store,
    server_config, conversation_manager, ConversationStoreImpl,
)
from openhands.server.types import AppMode
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.locations import get_conversation_dir, CONVERSATION_BASE_DIR
from openhands.utils.utils import create_registry_and_conversation_stats


TASK_TERMINAL_STATES = (TaskState.failed, TaskState.canceled, TaskState.completed, TaskState.rejected)
METADATA_NAME_PREFIX = "openhands"
AUTO_CONTINUE_RESPONSE = (
    'Please continue on whatever approach you think is suitable.\n'
    'If you think you have solved the task, please finish the interaction.\n'
    'IMPORTANT: YOU SHOULD NOT ASK FOR HUMAN RESPONSE UNTIL USER CONTACT YOU HIMSELF.\n'
)

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
            session: "A2AOHSessionWrapper",
            metadata: dict[str, Any] | None = None,
    ):

        if metadata is None:
            metadata: dict[str, Any] = dict()

        self.task_id = task_id
        self.status = TaskStatus(
            state=TaskState.submitted,
        )
        self.history: list[A2AMessage] = list()
        # TODO: Необходимо придумать способ хранить zip-архивы тасок
        #  на диске, чтобы в рамках одного контекста можно было отдавать разные
        #  снапшоты рабочей директории, а также сохранялось состояние при перезапуске OH.
        self.artifacts: list[Artifact] = list()
        self.min_event_id = -1
        self.max_event_id = -1
        self.session = session
        self.events: dict[int, Event] = dict()
        self.is_finished = False

        auto_continue = metadata.get(f"{METADATA_NAME_PREFIX}/auto-continue", False)
        show_all_events = metadata.get(f"{METADATA_NAME_PREFIX}/show-all-events", False)

        metadata[f"{METADATA_NAME_PREFIX}/auto-continue"] = auto_continue
        metadata[f"{METADATA_NAME_PREFIX}/show-all-events"] = show_all_events

        self.auto_continue = auto_continue
        self.show_all_events = show_all_events
        self.metadata = metadata

        session.add_task(self)

    def __repr__(self) -> str:
        return (f"Task(id={self.task_id}, status={self.status}, "
                f"history_length={len(self.history)}, metadata={self.metadata})")

    @property
    def context_id(self):
        return self.session.context_id

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
            message_id=f"{task_id}-{event.id}",
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
                        # TODO: Add log message
                        self.session.oh_session.agent_session.runtime.event_stream.add_event(
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

    # TODO: Reuse openhands.server.routes.files.py::zip_current_workspace::185 ?
    def zip_current_workspace(self) -> FileWithBytes | None:
        try:
            logger.debug('Zipping workspace')
            runtime: Runtime = self.session.oh_session.agent_session.runtime
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


class A2AOHSessionWrapper:

    def __init__(self, context_id: str | None):

        if context_id is None:
            context_id = uuid4().hex

        llm_registry, conversation_stats, _ = (
            create_registry_and_conversation_stats(config=config, sid=context_id, user_id=None)
        )

        self.context_id: str = context_id
        self.oh_session = A2AWebSession(
            sid=context_id,
            file_store=file_store,
            config=config,
            llm_registry=llm_registry,
            conversation_stats=conversation_stats,
            sio=None,
        )

        self.tasks: dict[str, A2AOHTaskWrapper] = dict()

        self.current_task: A2AOHTaskWrapper | None = None

    def is_started(self) -> bool:
        agent_session = self.oh_session.agent_session
        return agent_session.runtime is not None or agent_session.controller is not None

    def add_task(self, task: A2AOHTaskWrapper) -> bool:

        self.tasks[task.task_id] = task

        if self.current_task is not None and self.current_task.status.state not in TASK_TERMINAL_STATES:
            task.update_status(TaskState.rejected, text="You cannot run multiple tasks simultaneously in the context")
            return False

        self.current_task = task

        # TODO: Add check if agent name stay the same in metadata, otherwise throw an exception.

        self.oh_session.events_callback = task.on_event
        return True


@trace_class(kind=SpanKind.SERVER)
class A2aRequestHandler:

    _tasks: dict[str, A2AOHTaskWrapper] = dict()

    # TODO: Бесшовно интегрировать с имеющимися сессиями.
    #  Нужно подтягивать в том числе и обычные сессии, а не только A2A.
    _sessions: dict[str, A2AOHSessionWrapper] = dict()

    @classmethod
    def startup(cls) -> None:
        cls._sessions, cls._tasks = load_conversations(None)



    def _get_task_by_id(self, task_id: str) -> A2AOHTaskWrapper:
        task: A2AOHTaskWrapper | None = self._tasks.get(task_id, None)

        if task is None:
            raise ServerError(error=TaskNotFoundError())

        return task

    async def on_cancel_task(self, params: TaskIdParams, context) -> Task | None:
        # TODO: Check if it will work in case of task is canceled while runtime is starting.
        task = self._get_task_by_id(params.id)
        asyncio.create_task(
            self._background_task(params, task)
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
            if context_id is None or context_id not in self._sessions:
                # FIXME: Не уверен, что допустимо генерить context_id на стороне клиента
                session = A2AOHSessionWrapper(context_id=context_id)
                self._sessions[session.context_id] = session
            else:
                session = self._sessions[context_id]

            task = A2AOHTaskWrapper(task_id=task_id, session=session, metadata=copy(params.metadata))
            self._tasks[task.task_id] = task

        else:
            task = self._tasks[task_id]

            if task.status.state in TASK_TERMINAL_STATES:
                raise ServerError(error=InvalidParamsError(
                    message="The task already is in terminal state, you cannot interact it"
                ))

        user_message = copy(params.message)
        user_message.context_id = task.context_id
        task.history.append(user_message)

        if not task.session.is_started():
            task.history.append(
                A2AMessage(
                    context_id=task.context_id,
                    role=Role.agent,
                    parts=[TextPart(text="server is preparing")],
                    message_id=uuid4().hex,
                    task_id=task_id,
                    kind="message",
                )
            )

        # Update state after the input-required next message
        if task.status.state == TaskState.input_required:
            task.update_status(TaskState.working)

        asyncio.create_task(
            self._background_task(params, task)
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
        self, task: A2AOHTaskWrapper,
        params: MessageSendParams | TaskQueryParams | TaskIdParams,
    ):
        task_id = task.task_id

        # TODO: Fix user_id?
        settings_store = await SettingsStoreImpl.get_instance(config, user_id=task_id)
        settings = await settings_store.load()

        # TODO: Fix user_id?
        secrets_store = await SecretsStoreImpl.get_instance(config, user_id=task_id)
        user_secrets: Secrets | None = await secrets_store.load()

        if not settings:
            raise ConnectionRefusedError(
                'Settings not found', {'msg_id': 'CONFIGURATION$SETTINGS_NOT_FOUND'}
            )

        session_init_args: dict = {}
        if params.metadata is not None:
            agent_name = params.metadata.get(f'{METADATA_NAME_PREFIX}/agent', None)

            if agent_name is not None:
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

    async def _background_task(
            self,
            params: MessageSendParams | TaskQueryParams | TaskIdParams,
            task: A2AOHTaskWrapper
    ) -> None:

        oh_session = task.session.oh_session
        if not task.session.is_started():
            await oh_session.initialize_agent(
                await self._conversation_init_data_set(task, params), None, replay_json=None
            )
        data = self._convert_a2a_params_to_dict(params)
        await oh_session.dispatch(data)

        logger.debug(f"Finished background task for message {params}")


def _get_tasks_folder(sid: str, user_id: str | None):
    return f'{get_conversation_dir(sid, user_id)}tasks'

def _get_task_filename(sid: str, user_id: str | None, task_id: str):
    return f'{_get_tasks_folder(sid, user_id)}/{task_id}.json'

def save_task(task: A2AOHTaskWrapper, user_id: str | None):
    task_json = task.get_data().model_dump_json(exclude_none=True)
    filename = _get_task_filename(task.context_id, user_id, task.task_id)
    if len(task_json) > 1_000_000:
        logger.warning(
            f'Saving task JSON over 1MB: {len(task_json):,} bytes, filename: {filename}',
            extra={
                'user_id': user_id,
                'session_id': task.context_id,
                'size': len(task_json),
            },
        )
    file_store.write(filename, task_json)

def load_conversations(user_id: str | None) -> Tuple[dict[str, A2AOHSessionWrapper], dict[str, A2AOHTaskWrapper]]:
   try:
        sessions: dict[str, A2AOHSessionWrapper] = dict()
        loaded_tasks: dict[str, A2AOHTaskWrapper] = dict()
        #TODO: Загружать по-другому conversations?
        conversations = []
        if user_id:
            path = f'users/{user_id}'
            for c_path in list(file_store.list(path)):
                conversations.append(c_path.split("/")[2])
        else:
            path =  f'{CONVERSATION_BASE_DIR}'
            for c_path in list(file_store.list(path)):
                res = c_path.split("/")[1]
                if res != "users":
                    conversations.append(c_path.split("/")[1])

        for context_id in conversations:
            session = A2AOHSessionWrapper(context_id=context_id)
            sessions[session.context_id] = session
            loaded_tasks.update(load_tasks_json(context_id, session, None))
        return sessions, loaded_tasks
   except FileNotFoundError:
        return dict(), dict()

def load_tasks_json(sid: str, session: A2AOHSessionWrapper, user_id: str | None, ) -> dict[str, A2AOHTaskWrapper]:
    try:
        path = _get_tasks_folder(sid, user_id)
        files = file_store.list(path)
        tasks: dict[str, A2AOHTaskWrapper] = dict()
        for filename in files:
            content = file_store.read(filename)
            try:
                task_save = TaskSave.model_validate_json(content)
            except ValidationError:
                continue

            task = A2AOHTaskWrapper(task_id=task_save.id, session=session, metadata=copy(task_save.metadata))
            task.min_event_id = task_save.min_event_id
            task.max_event_id = task_save.max_event_id
            task.artifacts = copy(task_save.artifacts)
            task.status = task_save.status

            events: dict[int, Event] = dict()
            history: list[A2AMessage] = list()
            if task_save.min_event_id is not None and task_save.max_event_id is not None:
                loaded_events = list(session.oh_session.agent_session.event_stream.search_events(task_save.min_event_id,
                                                                                                 task_save.max_event_id))
                for event in loaded_events:
                    message = A2AMessage(
                        role=Role.user if event.source == EventSource.USER else Role.agent,
                        message_id=f"{task.task_id}-{event.id}",
                        context_id=sid,
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
            tasks[task.task_id] = task
    except FileNotFoundError:
        return dict()
    return tasks

A2aRequestHandler.startup()
