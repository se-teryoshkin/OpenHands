from __future__ import annotations

import asyncio
import json
import os
from io import TextIOWrapper
from typing import Any
from uuid import uuid4

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
    TaskIdParams,
    TaskState,
    TextPart,
)

from openhands.server.a2a.a2a_request_handler import TASK_TERMINAL_STATES
from openhands.server.a2a.agent_card import agent_card

TIMEOUT_SECONDS = 35.0
LOADING_TIMEOUT_SECONDS = 120.0


def _get_message() -> Message:
    return Message(
        role=Role.user,
        message_id=uuid4().hex,
        parts=[
            Part(
                root=TextPart(
                    text='Reply with exactly OK and then finish the task.'
                )
            ),
        ],
        metadata={
            'openhands/agent': 'CodeActAgent',
            'openhands/show-all-events': True,
            'openhands/auto-continue': True,
        }
    )


async def _wait_until_task_starts(client, task_id: str) -> Task:
    started_at = asyncio.get_running_loop().time()

    while True:
        task = await client.get_task(
            TaskQueryParams(
                id=task_id,
                metadata={'openhands/show-all-events': True},
            )
        )

        if task.status.state != TaskState.submitted:
            return task

        if asyncio.get_running_loop().time() - started_at >= LOADING_TIMEOUT_SECONDS:
            await client.cancel_task(TaskIdParams(id=task_id))
            pytest.fail(
                'Task stayed in submitted state too long before starting work: '
                f'{LOADING_TIMEOUT_SECONDS}s'
            )

        await asyncio.sleep(2)


async def _check_history_timeout(
        client,
        task_id: str,
        task: Task,
        timeout_state: dict[str, Any]
) -> None:
    current_event_id = None

    if task.history:
        current_event_id = (task.history[-1].metadata or {}).get('openhands/event-id')

    now = asyncio.get_running_loop().time()

    if current_event_id != timeout_state['last_event_id']:
        timeout_state['last_event_id'] = current_event_id
        timeout_state['last_change_at'] = now
        return

    if now - timeout_state['last_change_at'] >= TIMEOUT_SECONDS:
        await client.cancel_task(TaskIdParams(id=task_id))
        pytest.fail('Waiting for update in task exceed timeout')


def json_dumps(obj: Any) -> Any:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def json_dump_file(obj: Any, f: TextIOWrapper) -> Any:
    return json.dump(obj, f, ensure_ascii=False, indent=2)


@pytest.mark.asyncio
async def test_a2a_message_send():
    os.makedirs('test-results', exist_ok=True)

    print(f'Step 1: Connecting to A2A server')

    client = await ClientFactory.connect(
        agent=agent_card,
        client_config=ClientConfig(streaming=False),
    )

    print('Step 2: Sending message/send request...')
    response = None

    async for event in client.send_message(_get_message()):
        if response is not None:
            pytest.fail('Non-streaming send_message returned more than one response')
        response = event

    assert response is not None

    task = response[0]
    task_id = task.id

    with open('test-results/a2a_send_response.json', 'w', encoding='utf-8') as f:
        json_dump_file(task.model_dump(mode='json', exclude_none=True), f)

    print(f'Step 3: Waiting for task to start...')

    await _wait_until_task_starts(client, task_id)

    print(f'Step 4: Polling tasks/get for task_id={task_id}...')

    timeout_state = {
        'last_event_id': None,
        'last_change_at': asyncio.get_running_loop().time(),
    }

    while True:
        task = await client.get_task(
            TaskQueryParams(
                id=task_id,
                metadata={
                    'openhands/show-all-events': True,
                },
            )
        )
        last_task = task

        with open('test-results/a2a_get_task_last_response.json', 'w', encoding='utf-8') as f:
            json_dump_file(task.model_dump(mode='json', exclude_none=True), f)

        final_state = task.status.state

        if final_state in TASK_TERMINAL_STATES:
            break

        await _check_history_timeout(
            client=client,
            task_id=task_id,
            task=task,
            timeout_state=timeout_state,
        )
        await asyncio.sleep(2)

    assert last_task is not None

    print('Step 5: Verifying result...')
    assert final_state == 'completed', (
        f'Expected completed, got {final_state}: {last_task}'
    )


@pytest.mark.asyncio
async def test_a2a_message_stream():
    os.makedirs('test-results', exist_ok=True)

    print('Step 1: Connecting to A2A server')

    client = await ClientFactory.connect(
        agent=agent_card,
        client_config=ClientConfig(streaming=True,
                                   httpx_client=httpx.AsyncClient(
                                       timeout=httpx.Timeout(connect=30.0, read=360.0, write=30.0, pool=30.0)
                                   )),

    )

    print('Step 2: Sending message/stream request...')

    events = []
    task, update = None, None
    event_dump = {}

    async for event in client.send_message(_get_message()):
        task, update = event

        event_dump = {
            'task': task.model_dump(mode='json', exclude_none=True),
            'update': None if update is None else update.model_dump(mode='json', exclude_none=True),
        }

        events.append(event_dump)

        with open('test-results/a2a_stream_last_event.json', 'w', encoding='utf-8') as f:
            json_dump_file(event_dump, f)

        if update is None:
            continue

        if update.final is True:
            break

    with open('test-results/a2a_stream_events.json', 'w', encoding='utf-8') as f:
        json_dump_file(events, f)

    print('Step 3: Verifying result...')
    assert task.status.state == 'completed', (
        f'Expected completed, got {task.status.state}: {event_dump}'
    )
