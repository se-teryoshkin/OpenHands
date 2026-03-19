from __future__ import annotations

import asyncio
import json
import os
from io import TextIOWrapper
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Message,
    Part,
    Role,
    Task,
    TaskQueryParams,
    TextPart,
)

from openhands.server.a2a.a2a_request_handler import TASK_TERMINAL_STATES

def _normalize_base_url(base_url: str | None) -> str:
    value = (base_url or os.getenv('OPENHANDS_A2A_URL') or 'http://127.0.0.1:3000').strip()
    if not value.startswith(('http://', 'https://')):
        value = f'http://{value}'
    return value.rstrip('/')

def _get_agent_card(a2a_url: str) -> AgentCard:
    return AgentCard(
            name='OpenHands A2A',
            description='OpenHands A2A endpoint',
            url=a2a_url,
            version='1.0.0',
            default_input_modes=['text'],
            default_output_modes=['text'],
            capabilities=AgentCapabilities(streaming=True),
            skills=[],
        )
def json_dumps(obj: Any) -> Any:
    return json.dumps(obj, ensure_ascii=False, indent=2)

def json_dump_file(obj: Any, f: TextIOWrapper) -> Any:
    return json.dump(obj, f, ensure_ascii=False, indent=2)

message = Message(
    role=Role.user,
    message_id='msg',
    parts=[
        Part(
            root=TextPart(
                text='Reply with exactly OK and then finish the task.'
            )
        ),
    ],
    metadata ={
        'openhands/agent': 'CodeActAgent',
        'openhands/show-all-events': True,
        'openhands/auto-continue': True,
    }
)


@pytest.mark.asyncio
async def test_a2a_message_send(base_url: str):
    os.makedirs('test-results', exist_ok=True)

    resolved_base_url = _normalize_base_url(base_url)

    print(f'Step 1: Connecting to A2A server via {resolved_base_url}/a2a...')

    client = await ClientFactory.connect(
        agent=_get_agent_card(f"{resolved_base_url}/a2a"),
        client_config=ClientConfig(streaming=False),
    )

    print('Step 2: Sending message/send request...')
    events = []
    task_id = None
    message_text = None

    async for event in client.send_message(message):
        if isinstance(event, tuple):
            task, update = event

            task_dump = task.model_dump(mode='json', exclude_none=True)

            event_dump = {
                'task': task_dump,
            }

            if task_id is None:
                task_id = task.id
        else:
            event_dump = event.model_dump(mode='json', exclude_none=True)
            message_text = json_dumps(event_dump)

        if event_dump not in events:
            events.append(event_dump)
            print(json_dumps(event_dump))

    with open('test-results/a2a_send_response.json', 'w', encoding='utf-8') as f:
        json_dump_file(events, f)

    assert events, 'No events returned from client.send_message(...)'

    if message_text is not None:
        assert 'OK' in message_text, (
            f'Expected OK in message, got: {message_text}'
        )
        return

    assert task_id, json_dumps(events)

    print(f'Step 3: Polling tasks/get for task_id={task_id}...')
    last_task_dump = None
    final_state = None

    for attempt in range(60):
        task = await client.get_task(
            TaskQueryParams(
                id=task_id,
                history_length=50,
                show_all_events=True
            )
        )

        last_task_dump = task.model_dump(mode='json', exclude_none=True)

        with open('test-results/a2a_get_task_last_response.json', 'w', encoding='utf-8') as f:
            json_dump_file(last_task_dump, f)

        final_state = task.status.state

        if final_state in TASK_TERMINAL_STATES:
            break

        await asyncio.sleep(2)
    else:
        pytest.fail('Task did not reach terminal state')

    assert last_task_dump is not None

    final_text = json_dumps(last_task_dump)

    print('Step 4: Verifying final task result...')
    assert final_state == 'completed', (
        f'Expected completed, got {final_state}: {final_text}'
    )
    assert 'OK' in final_text, (
        f'Expected OK in final task output, got: {final_text}'
    )


@pytest.mark.asyncio
async def test_a2a_message_stream(base_url: str):
    os.makedirs('test-results', exist_ok=True)

    resolved_base_url = _normalize_base_url(base_url)

    print(f'Step 1: Connecting to A2A server via {resolved_base_url}/a2a...')

    client = await ClientFactory.connect(
        agent=_get_agent_card(f"{resolved_base_url}/a2a"),
        client_config=ClientConfig(streaming=True,
                                   httpx_client=httpx.AsyncClient(
                                       timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
                                   )),
    )

    print('Step 2: Sending message/stream request...')
    events = []
    task_id = None
    message_text = None
    saw_update = False

    async for event in client.send_message(message):
        if isinstance(event, tuple):
            task, update = event

            task_dump = task.model_dump(mode='json', exclude_none=True)

            event_dump = {
                'task': task_dump
            }

            if task_id is None:
                task_id = task.id
                saw_update = True

        else:
            event_dump = event.model_dump(mode='json', exclude_none=True)
            message_text = json_dumps(event_dump)

        if event_dump not in events:
            events.append(event_dump)
            print(json_dumps(event_dump))


    with open('test-results/a2a_stream_events.json', 'w', encoding='utf-8') as f:
        json_dump_file(events, f)

    assert events, 'No events returned from client.send_message(...)'
    assert saw_update or task_id is not None, json_dumps(events)

    if message_text is not None and task_id is None:
        assert 'OK' in message_text, (
            f'Expected OK in message, got: {message_text}'
        )
        return

    assert task_id, json_dumps(events)

    print(f'Step 3: Polling tasks/get for task_id={task_id}...')
    last_task_dump = None
    final_state = None

    for attempt in range(60):
        task = await client.get_task(
            TaskQueryParams(
                id=task_id,
                history_length=50,
                show_all_events=True,
            )
        )

        last_task_dump = task.model_dump(mode='json', exclude_none=True)

        with open('test-results/a2a_stream_last_response.json', 'w', encoding='utf-8') as f:
            json_dump_file(last_task_dump, f)

        print(json_dumps(last_task_dump))

        final_state = task.status.state
        if final_state in TASK_TERMINAL_STATES:
            break

        await asyncio.sleep(2)
    else:
        pytest.fail('Streaming task did not reach terminal state')

    assert last_task_dump is not None

    final_text = json_dumps(last_task_dump)

    print('Step 4: Verifying final streaming task result...')

    assert final_state == 'completed', f'Expected completed, got {final_state}: {final_text}'
    assert 'OK' in final_text, f'Expected OK in final task output, got: {final_text}'
