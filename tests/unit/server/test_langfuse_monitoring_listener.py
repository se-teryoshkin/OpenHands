import pytest

from openhands.core.config.langfuse_config import LangfuseConfig
from openhands.events.action import MessageAction
from openhands.llm.metrics import Metrics
from openhands.server import langfuse_monitoring_listener as langfuse_module
from openhands.server.langfuse_monitoring_listener import LangfuseMonitoringListener


class _FakeLangfuseClient:
    def __init__(self):
        self.events: list[dict] = []

    def create_event(self, **kwargs):
        self.events.append(kwargs)


@pytest.fixture()
def listener(monkeypatch):
    # Ensure trace IDs are deterministic for assertions
    monkeypatch.setattr(
        langfuse_module.Langfuse,
        'create_trace_id',
        staticmethod(lambda seed=None: 'trace-id'),
    )
    client = _FakeLangfuseClient()
    cfg = LangfuseConfig(default_tags=['test'])
    return LangfuseMonitoringListener(client, cfg), client


def test_on_session_event_captures_llm_metrics(listener):
    monitoring_listener, client = listener
    event = MessageAction(content='hello world')
    metrics = Metrics(model_name='test-model')
    metrics.add_cost(0.1)
    event.llm_metrics = metrics

    monitoring_listener.on_session_event('conversation-1', event)

    assert len(client.events) == 1
    recorded = client.events[0]
    assert recorded['metadata']['conversation_id'] == 'conversation-1'
    assert recorded['metadata']['event_type'] == 'MessageAction'
    assert recorded['metadata']['llm_metrics']['accumulated_cost'] == pytest.approx(
        0.1
    )


def test_on_agent_session_start(listener):
    monitoring_listener, client = listener
    monitoring_listener.on_agent_session_start(
        success=True,
        duration=3.5,
        conversation_id='conversation-1',
        user_id='user-123',
    )

    assert client.events[-1]['name'] == 'agent_session_start'
    assert client.events[-1]['metadata']['success'] is True


def test_on_create_conversation(listener):
    monitoring_listener, client = listener
    monitoring_listener.on_create_conversation(
        conversation_id='conversation-1', user_id='user-123'
    )

    assert client.events[-1]['name'] == 'conversation_created'
    assert client.events[-1]['metadata']['user_id'] == 'user-123'

