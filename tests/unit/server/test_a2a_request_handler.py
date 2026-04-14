from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from a2a.types import (
    Message,
    MessageSendParams,
    Task,
    TaskIdParams,
    TaskNotFoundError,
    TaskQueryParams,
    TaskState,
    TextPart,
    UnsupportedOperationError,
    InternalError,
    InvalidParamsError,
)
from a2a.utils.errors import (
    ServerError,
)

import openhands.server.a2a.a2a_request_handler as a2a_handler
from openhands.core.schema import ActionType
from openhands.core.schema.agent import AgentState
from openhands.events.action import NullAction, MessageAction
from openhands.events.event import EventSource
from openhands.server.a2a.a2a_request_handler import (
    A2aRequestHandler,
    A2AOHTaskWrapper,
    METADATA_NAME_PREFIX
)

DEFAULT_TASK_ID = uuid4().hex
DEFAULT_CONTEXT_ID = uuid4().hex


def _get_default_message() -> Message:
    return Message(
        role="user",
        parts=[TextPart(text="hello")],
        message_id=uuid4().hex,
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        kind="message",
    )


def _get_no_context_message() -> Message:
    return Message(
        role="user",
        parts=[TextPart(text="hello")],
        message_id=uuid4().hex,
        task_id=None,
        context_id=None,
        kind="message",
    )


@pytest.fixture(autouse=True)
def _isolate_handler_state(monkeypatch):
    A2aRequestHandler._tasks = {}
    A2aRequestHandler._current_session_tasks = {}

    monkeypatch.setattr(a2a_handler, "save_task", lambda *args, **kwargs: None)

    def _fake_create_task(coro):
        coro.close()
        return MagicMock()

    monkeypatch.setattr(a2a_handler.asyncio, "create_task", _fake_create_task)

    yield


@pytest.mark.asyncio
async def test_on_get_task():
    handler = A2aRequestHandler()

    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={},
    )

    task.status.state = TaskState.working
    A2aRequestHandler._tasks[DEFAULT_TASK_ID] = task

    params = TaskQueryParams(id=DEFAULT_TASK_ID)
    result = await handler.on_get_task(params, MagicMock())
    assert result.status.state == TaskState.working


@pytest.mark.asyncio
async def test_get_task_session_missing(monkeypatch):
    handler = A2aRequestHandler()

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: None,
        raising=False,
    )

    handler.create_new_conversation = AsyncMock(return_value=None)
    params = MessageSendParams(message=_get_default_message(), metadata={})
    task, context_id = await handler.get_task(params)

    assert context_id == DEFAULT_CONTEXT_ID
    assert task.context_id == DEFAULT_CONTEXT_ID
    assert task.task_id in A2aRequestHandler._tasks
    handler.create_new_conversation.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_task_task_id_none(monkeypatch):
    handler = A2aRequestHandler()

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: MagicMock(),
        raising=False,
    )

    msg = _get_default_message()
    msg.task_id = None

    params = MessageSendParams(message=msg, metadata={})
    task, context_id = await handler.get_task(params)

    assert context_id == DEFAULT_CONTEXT_ID
    assert task.task_id is not None
    assert task.task_id in A2aRequestHandler._tasks


@pytest.mark.asyncio
async def test_cancel_task():
    handler = A2aRequestHandler()

    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={},
    )

    A2aRequestHandler._tasks[DEFAULT_TASK_ID] = task

    params = TaskIdParams(id=DEFAULT_TASK_ID)
    result = await handler.on_cancel_task(params, MagicMock())

    assert result.status.state == TaskState.canceled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "params"),
    [
        ("on_get_task", TaskQueryParams(id="test-task-id")),
        ("on_cancel_task", TaskIdParams(id="test-task-id")),
    ],
)
async def test_task_methods_raise_not_found(method_name, params):
    handler = A2aRequestHandler()
    method = getattr(handler, method_name)

    with pytest.raises(ServerError) as exc_info:
        await method(params, MagicMock())

    assert isinstance(exc_info.value.error, TaskNotFoundError)


@pytest.mark.asyncio
async def test_on_message_send(monkeypatch):
    handler = A2aRequestHandler()
    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: MagicMock(),
        raising=False,
    )
    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={},
    )

    task.status.state = TaskState.input_required
    A2aRequestHandler._tasks[DEFAULT_TASK_ID] = task

    params = MessageSendParams(message=_get_default_message(), metadata={})

    result = await handler.on_message_send(params, MagicMock())
    assert result.id == DEFAULT_TASK_ID
    assert result.status.state == TaskState.working


@pytest.mark.asyncio
async def test_on_message_send_parallel_task(monkeypatch):
    handler = A2aRequestHandler()

    running = A2AOHTaskWrapper(task_id=DEFAULT_TASK_ID, context_id=DEFAULT_CONTEXT_ID, metadata={})
    running.status.state = TaskState.working
    A2aRequestHandler._current_session_tasks[DEFAULT_CONTEXT_ID] = running

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: MagicMock(),
        raising=False,
    )

    create_task_mock = MagicMock()
    monkeypatch.setattr(a2a_handler.asyncio, "create_task", create_task_mock)
    params = MessageSendParams(message=_get_default_message(), metadata={})

    result = await handler.on_message_send(params, MagicMock())

    assert result.status.state == TaskState.rejected
    assert create_task_mock.call_count == 0


@pytest.mark.asyncio
async def test_on_message_send_new(monkeypatch):
    handler = A2aRequestHandler()

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: MagicMock(),
        raising=False,
    )
    params = MessageSendParams(message=_get_default_message(), metadata={})

    result = await handler.on_message_send(params, MagicMock())
    assert result.id == DEFAULT_TASK_ID
    assert result.status.state == TaskState.submitted


def test_convert_a2a_params_to_dict_message_send():
    params = MessageSendParams(message=_get_default_message())
    handler = A2aRequestHandler()
    result = handler._convert_a2a_params_to_dict(params)

    assert result["action"] == ActionType.MESSAGE
    assert result["args"]["content"] == "hello"
    assert result["source"] == EventSource.USER


@pytest.mark.parametrize(
    "params",
    [
        TaskQueryParams(id="dummy-task"),
        TaskIdParams(id="dummy-task"),
    ],
)
def test_convert_a2a_params_to_dict(params):
    handler = A2aRequestHandler()
    result = handler._convert_a2a_params_to_dict(params)

    assert result["action"] == ActionType.CHANGE_AGENT_STATE
    assert result["args"]["agent_state"] == AgentState.STOPPED
    assert result["source"] == EventSource.USER


def test_convert_a2a_params_to_dict_invalid_type():
    class Dummy:
        pass

    handler = A2aRequestHandler()
    with pytest.raises(ServerError) as e:
        handler._convert_a2a_params_to_dict(Dummy())
    assert isinstance(e.value.error, UnsupportedOperationError)


@pytest.mark.asyncio
async def test_conversation_init_data(monkeypatch):
    handler = A2aRequestHandler()

    settings = SimpleNamespace(s1='v1')
    settings_instance = AsyncMock()
    settings_instance.load = AsyncMock(return_value=settings)

    monkeypatch.setattr(
        'openhands.server.shared.SettingsStoreImpl.get_instance',
        AsyncMock(return_value=settings_instance),
    )

    secrets = SimpleNamespace(
        provider_tokens=MappingProxyType({'gh': 'tok'}),
        custom_secrets=MappingProxyType({'x': 'y'}),
    )
    secrets_instance = AsyncMock()
    secrets_instance.load = AsyncMock(return_value=secrets)

    monkeypatch.setattr(
        'openhands.server.shared.SecretsStoreImpl.get_instance',
        AsyncMock(return_value=secrets_instance),
    )

    monkeypatch.setattr(
        'openhands.server.shared.server_config', SimpleNamespace(app_mode='local')
    )

    params = TaskQueryParams(id='tid')
    params.metadata = {f"{a2a_handler.METADATA_NAME_PREFIX}/agent": "CodeActAgent"}

    result = await handler._conversation_init_data_set(params)

    assert result.agent == 'CodeActAgent'


@pytest.mark.asyncio
async def test_conversation_init_no_settings(monkeypatch):
    handler = A2aRequestHandler()

    params = TaskQueryParams(id='tid')
    params.metadata = {f"{a2a_handler.METADATA_NAME_PREFIX}/agent": "CodeActAgent"}

    settings_instance = AsyncMock()
    settings_instance.load = AsyncMock(return_value=None)
    monkeypatch.setattr(
        'openhands.server.shared.SettingsStoreImpl.get_instance',
        AsyncMock(return_value=settings_instance),
    )

    with pytest.raises(ConnectionRefusedError) as exc_info:
        await handler._conversation_init_data_set(params)

    assert 'Settings not found' in str(exc_info.value)


@pytest.mark.asyncio
async def test_on_message_send_stream(monkeypatch):
    handler = A2aRequestHandler()

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: MagicMock(),
        raising=False,
    )
    params = MessageSendParams(message=_get_default_message(), metadata={})

    gen = handler.on_message_send_stream(params, MagicMock())
    first = await gen.__anext__()
    assert first.kind == "status-update"
    await gen.aclose()


@pytest.mark.asyncio
async def test_on_resubscribe_to_task():
    handler = A2aRequestHandler()
    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={},
    )
    A2aRequestHandler._tasks[DEFAULT_TASK_ID] = task

    msg1 = SimpleNamespace(
        kind="message",
        metadata={f"{METADATA_NAME_PREFIX}/event-id": 1},
    )
    msg2 = SimpleNamespace(
        kind="message",
        metadata={f"{METADATA_NAME_PREFIX}/event-id": 2},
    )
    task.history = [msg1, msg2]
    task.events[1] = MessageAction(content="msg1", image_urls=[])
    task.events[2] = MessageAction(content="msg2", image_urls=[])

    task.last_streamed_event_id = 1
    A2aRequestHandler._tasks[DEFAULT_TASK_ID] = task

    gen = handler.on_resubscribe_to_task(TaskIdParams(id=DEFAULT_TASK_ID), MagicMock())
    first = await gen.__anext__()
    assert first is msg2

    second = await gen.__anext__()
    assert second.kind == "status-update"

    await gen.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args", "is_async"),
    [
        ("on_set_task_push_notification_config", (None,), True),
        ("on_get_task_push_notification_config", (TaskIdParams(id="test-task-id"),), True),
        ("on_list_task", (), True),
        ("should_add_push_info", (TaskIdParams(id="test-task-id"),), False),
    ],
)
async def test_unsupported(method_name, args, is_async):
    handler = A2aRequestHandler()
    method = getattr(handler, method_name)

    with pytest.raises(ServerError) as exc_info:
        if is_async:
            await method(*args)
        else:
            method(*args)

    assert isinstance(exc_info.value.error, UnsupportedOperationError)


def test_check_if_no_tasks_running_for_context():
    handler = A2aRequestHandler()

    current = A2AOHTaskWrapper(task_id="t1", context_id=DEFAULT_CONTEXT_ID, metadata={})
    current.status.state = TaskState.working
    A2aRequestHandler._current_session_tasks[DEFAULT_CONTEXT_ID] = current

    new_task = A2AOHTaskWrapper(task_id="t2", context_id=DEFAULT_CONTEXT_ID, metadata={})

    assert handler.check_if_no_tasks_running_for_context(new_task, DEFAULT_CONTEXT_ID) is False
    assert new_task.status.state == TaskState.rejected


@pytest.mark.asyncio
async def test_get_task_with_no_context_id(monkeypatch):
    handler = A2aRequestHandler()

    monkeypatch.setattr(
        a2a_handler.conversation_manager,
        "get_agent_session",
        lambda _cid: None,
        raising=False,
    )

    handler.create_new_conversation = AsyncMock(return_value=None)

    params = MessageSendParams(message=_get_no_context_message(), metadata={})

    task_check, context_id_check = await handler.get_task(params)

    assert task_check.task_id in A2aRequestHandler._tasks
    assert context_id_check is not None
    assert task_check.context_id == context_id_check
    handler.create_new_conversation.assert_awaited_once()


@pytest.mark.parametrize(
    ("task_event", "should_pass"),
    [
        (NullAction(), False),
        (MessageAction(content="hi", image_urls=[]), True),
    ],
    ids=["system", "visible"],
)
def test_filter_event(task_event, should_pass):
    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={},
    )

    event = SimpleNamespace(
        kind="message",
        metadata={f"{METADATA_NAME_PREFIX}/event-id": 1},
    )
    task.events[1] = task_event

    result = a2a_handler._filter_event(task, event, show_all_events=False)

    if should_pass:
        assert result is event
    else:
        assert result is None


def test_task_serialize():
    task = A2AOHTaskWrapper(
        task_id=DEFAULT_TASK_ID,
        context_id=DEFAULT_CONTEXT_ID,
        metadata={f"{METADATA_NAME_PREFIX}/agent": "CodeActAgent", "k": "v"},
    )
    task.status.state = TaskState.input_required

    data = task.serialize()
    loaded = A2AOHTaskWrapper.deserialize(data, user_id=None)

    assert loaded.task_id == DEFAULT_TASK_ID
    assert loaded.context_id == DEFAULT_CONTEXT_ID
    assert loaded.status.state == TaskState.input_required
    assert loaded.metadata["k"] == "v"
    assert loaded.metadata[f"{METADATA_NAME_PREFIX}/agent"] == "CodeActAgent"

    assert loaded.events == {}
    assert loaded.history == []
