import asyncio
from collections.abc import AsyncGenerator
from copy import copy
from datetime import datetime, timezone, UTC
from typing import Any, Dict
from uuid import uuid4

from a2a.server.events import Event as A2AEvent
from a2a.types import (
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
)
from a2a.utils.errors import ServerError
from a2a.utils.telemetry import SpanKind, trace_class

from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import ActionType
from openhands.core.schema.agent import AgentState
from openhands.events import EventStreamSubscriber
from openhands.events.action import NullAction, SystemMessageAction, RecallAction, Action, ChangeAgentStateAction, \
    MessageAction
from openhands.events.event import Event, EventSource
from openhands.events.event_store import EventStore
from openhands.events.async_event_store_wrapper import AsyncEventStoreWrapper

from openhands.events.observation import NullObservation
from openhands.events.observation.agent import AgentStateChangedObservation, RecallObservation
from openhands.events.serialization import event_from_dict, event_to_dict
from openhands.integrations.provider import ProviderHandler, PROVIDER_TOKEN_TYPE
from openhands.server.routes.manage_conversations import InitSessionRequest
from openhands.server.services.conversation_service import create_new_conversation
from openhands.server.session.agent_session import AgentSession
from openhands.server.session.conversation_init_data import ConversationInitData
from openhands.server.shared import (
    SecretsStoreImpl,
    SettingsStoreImpl,
    config,
    server_config, conversation_manager
)
from openhands.server.types import AppMode
from openhands.server.user_auth import AuthType
from openhands.storage.data_models.conversation_metadata import ConversationTrigger
from openhands.storage.data_models.secrets import Secrets
from openhands.utils.utils import create_registry_and_conversation_stats

TASK_TERMINAL_STATES = (TaskState.failed, TaskState.canceled, TaskState.completed, TaskState.rejected)
METADATA_NAME_PREFIX = "openhands"
AUTO_CONTINUE_RESPONSE = (
    'Please continue on whatever approach you think is suitable.\n'
    'If you think you have solved the task, please finish the interaction.\n'
    'IMPORTANT: YOU SHOULD NOT ASK FOR HUMAN RESPONSE UNTIL USER CONTACT YOU HIMSELF.\n'
)

A2A_UPDATED_AT_CALLBACK_ID = 'a2a_updated_at_callback_id'

class A2AOHTaskWrapper:

    def __init__(
            self,
            task_id: str,
            metadata: dict[str, Any] | None = None,
    ):

        self.first = False
        if metadata is None:
            metadata: dict[str, Any] = dict()

        self.task_id = task_id
        self.status = TaskStatus(
            state=TaskState.submitted,
        )
        self.history: list[A2AMessage] = list()
        self.min_event_id = -1
        self.max_event_id = -1
        self.agent_session = None
        self.events: dict[int, Event] = dict()
        self.is_finished = False

        auto_continue = metadata.get(f"{METADATA_NAME_PREFIX}/auto-continue", False)
        show_all_events = metadata.get(f"{METADATA_NAME_PREFIX}/show-all-events", False)

        metadata[f"{METADATA_NAME_PREFIX}/auto-continue"] = auto_continue
        metadata[f"{METADATA_NAME_PREFIX}/show-all-events"] = show_all_events

        self.auto_continue = auto_continue
        self.show_all_events = show_all_events
        self.metadata = metadata

        # TODO: Add artifacts (zip-file with code).
        #  Reference: openhands.server.routes.files.py::zip_current_workspace::185

    def set_session(self, session: "A2AOHSessionWrapper"):
        self.agent_session = session
        self.agent_session.add_task(self)

    def __repr__(self) -> str:
        return (f"Task(id={self.task_id}, status={self.status}, "
                f"history_length={len(self.history)}, metadata={self.metadata})")

    @property
    def context_id(self):
        return self.agent_session.context_id

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

    def to_response(self, history_length: int | None = None) -> Task:

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

        if history_length is not None and history_length > 0:
            history = self.history[-history_length:]
        else:
            history = self.history

        return Task(
            id=self.task_id,
            context_id=self.context_id,
            status=self.status,
            history=history,
            metadata=copy(self.metadata),
        )

    def on_event(self, event: Event) -> None:

        print("ON EVENT")
        print(event_to_dict(event))

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

        # TODO: Сохранять все события, но фильтрацию выполнять только при отправке клиенту
        #  в потоке или при tasks/get + метаданные
        if not isinstance(event, (
            # System events
            NullAction,
            NullObservation,
            AgentStateChangedObservation,
            SystemMessageAction,
            RecallAction,
            RecallObservation,
            ChangeAgentStateAction,
        )) or self.show_all_events:
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
                        self.agent_session.session.event_stream.add_event(
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
            # TODO: # Add finish processing (for example, close stream, create artifacts, etc)
            ...
            # self.agent_session.session.event_stream.unsubscribe(
            #     EventStreamSubscriber.SERVER,
            #     A2A_UPDATED_AT_CALLBACK_ID
            # )

class A2AOHSessionWrapper:

    def __init__(self, session: AgentSession, context_id: str | None):

        if context_id is None:
            context_id = uuid4().hex

        llm_registry, conversation_stats, _ = (
            create_registry_and_conversation_stats(config=config, sid=context_id, user_id=None)
        )

        self.context_id: str = context_id
        self.session = session
        self.tasks: dict[str, A2AOHTaskWrapper] = dict()

        self.current_task: A2AOHTaskWrapper | None = None

    def is_started(self) -> bool:
        agent_session = self.session
        return agent_session.runtime is not None or agent_session.controller is not None

    def add_task(self, task: A2AOHTaskWrapper) -> bool:

        self.tasks[task.task_id] = task

        if self.current_task is not None and self.current_task.status.state not in TASK_TERMINAL_STATES:
            task.update_status(TaskState.rejected, text="You cannot run multiple tasks simultaneously in the context")
            return False

        self.current_task = task

        # TODO: Add check if agent name stay the same in metadata, otherwise throw an exception.


        conversation_manager.get_agent_session(task.context_id).event_stream.subscribe(
                EventStreamSubscriber.SERVER,
                task.on_event,
                A2A_UPDATED_AT_CALLBACK_ID,
            )
        event_store = EventStore(
            task.context_id, conversation_manager.file_store, user_id=None
        )
        for event in event_store.search_events():
            print(event_to_dict(event))

        return True


@trace_class(kind=SpanKind.SERVER)
class A2aRequestHandler:

    _tasks: dict[str, A2AOHTaskWrapper] = dict()

    # TODO: Бесшовно интегрировать с имеющимися сессиями.
    #  Нужно подтягивать в том числе и обычные сессии, а не только A2A.
    _sessions: dict[str, A2AOHSessionWrapper] = dict()

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
        task = A2AOHTaskWrapper(task_id=task_id, metadata=copy(params.metadata))

        if task_id not in self._tasks:
            if context_id is None or context_id not in self._sessions:

                # FIXME: Не уверен, что допустимо генерить context_id на стороне клиента
                init_data = await self._conversation_init_data_set(task=task, params=params)

                init_session_req = InitSessionRequest(
                        repository=init_data.selected_repository,
                        git_provider=init_data.git_provider,
                        selected_branch=init_data.selected_branch,
                        initial_user_msg=None,
                        image_urls=None,
                        replay_json=init_data.replay_json,
                        conversation_instructions=init_data.conversation_instructions)

                context_id =  getattr(init_session_req, 'conversation_id', None) or uuid4().hex
                initial_user_msg = params.message.parts[0].root.text

                secrets_store = await SecretsStoreImpl.get_instance(config, user_id=task_id)
                user_secrets: Secrets | None = await secrets_store.load()
                #TODO:add to background?

                await self._new_conversation(task=task,
                                             params=params,
                                             context_id=context_id,
                                             user_id=None)

                print("created new conversation")
                session = A2AOHSessionWrapper(conversation_manager.get_agent_session(context_id), context_id=context_id)
                task.first = True
                self._sessions[context_id] = session


            else:
                session = self._sessions[context_id]

            task.set_session(session)
            self._tasks[task.task_id] = task

            data = self._convert_a2a_params_to_dict(params)
            await self.dispatch(data, task)

        else:
            task = self._tasks[task_id]

            if task.status.state in TASK_TERMINAL_STATES:
                raise ServerError(error=InvalidParamsError(
                    message="The task already is in terminal state, you cannot interact it"
                ))
            session = task.agent_session

        user_message = copy(params.message)
        user_message.context_id = task.context_id
        task.history.append(user_message)

        if not task.agent_session.is_started():
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

        # asyncio.create_task(
        #     self._background_task(params, task)
        # )

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
        return self._get_task_by_id(params.id).to_response(params.history_length)

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

    async def _new_conversation(self,
                                task: A2AOHTaskWrapper,
                                params: MessageSendParams | TaskQueryParams | TaskIdParams,
                                context_id: str,
                                user_id: str | None = None,
                                auth_type: AuthType | None = None
                                ):

        data = await self._conversation_init_data_set(task=task, params=params)

        logger.info(f'initializing_new_conversation:{data}')
        repository = data.selected_repository
        selected_branch = data.selected_branch
        image_urls = []
        replay_json = data.replay_json
        suggested_task = None
        create_microagent = None
        git_provider = data.git_provider
        conversation_instructions = data.conversation_instructions

        conversation_trigger = ConversationTrigger.SUGGESTED_TASK

        # if suggested_task:
        #     initial_user_msg = suggested_task.get_prompt_for_task()
        #     conversation_trigger = ConversationTrigger.SUGGESTED_TASK
        # elif create_microagent:
        #     conversation_trigger = ConversationTrigger.MICROAGENT_MANAGEMENT
        #     # Set repository and git_provider from create_microagent if not already set
        #     if not repository and create_microagent.repo:
        #         repository = create_microagent.repo
        #     if not git_provider and create_microagent.git_provider:
        #         git_provider = create_microagent.git_provider

        if auth_type == AuthType.BEARER:
            conversation_trigger = ConversationTrigger.REMOTE_API_KEY

        try:
            if repository:
                provider_handler = ProviderHandler(data.git_provider_tokens)
                # Check against git_provider, otherwise check all provider apis
                await provider_handler.verify_repo_provider(repository, git_provider)

            conversation_id = context_id
            agent_loop_info = await create_new_conversation(
                user_id=user_id,
                git_provider_tokens=data.git_provider_tokens,
                custom_secrets=data.custom_secrets,
                selected_repository=repository,
                selected_branch=selected_branch,
                initial_user_msg="",
                image_urls=image_urls,
                replay_json=replay_json,
                conversation_trigger=conversation_trigger,
                conversation_instructions=conversation_instructions,
                git_provider=git_provider,
                conversation_id=conversation_id,
                mcp_config=data.mcp_config,
            )
        except RuntimeError as e:
            logger.error(f"Exception on init conversation: {e}")


    async def _background_task(
            self,
            params: MessageSendParams | TaskQueryParams | TaskIdParams,
            task: A2AOHTaskWrapper
    ) -> None:

        # if not task.agent_session.is_started():
        #     init_data = await self._conversation_init_data_set(task=task, params=params)
        #     await self._new_conversation(init_session_req, task.context_id)
        data = self._convert_a2a_params_to_dict(params)

        if task.status.state == TaskState.input_required:
            task.update_status(TaskState.working)

        await self.dispatch(data, task)
        logger.debug(f"Finished background task for message {params}")



    async def dispatch(self, data: dict, task: A2AOHTaskWrapper) -> None:
        event = event_from_dict(data.copy())
        try:
            await conversation_manager.send_event_to_conversation(
                task.context_id, data.copy()
            )

        except Exception as e:
            logger.error(f'Error adding message to conversation: {e}')
        print("DIspatch event")
