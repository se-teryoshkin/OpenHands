## A2A Request Handler

A2A Request Handler implements Agent2Agent (A2A) Protocol into OpenHands SDK

## Supported operations

Supported:

- `message/send`
- `message/stream`
- `tasks/get`
- `tasks/resubscribe`
- `tasks/cancel`

Not supported:

- `tasks/list`
- `tasks/pushNotificationConfig/get`
- `tasks/pushNotificationConfig/set`

## How it works

Each A2A task is wrapped by `A2AOHTaskWrapper`

`A2AOHTaskWrapper` stores:

- `task_id` — the A2A task ID
- `context_id` — the OpenHands conversation ID
- `message_id` — the current user message ID
- task status
- task history
- metadata
- artifacts
- active stream subscribers

Only one active task is allowed per `context_id`

## Task states

A task can move through these states:

- `submitted`
- `working`
- `input_required`
- `completed`
- `failed`
- `canceled`
- `rejected`

Terminal states:

- `completed`
- `failed`
- `canceled`
- `rejected`

### Internal state mapping

OpenHands agent states are mapped to A2A task states like this:

| OpenHands agent state        | A2A task state   |
|------------------------------|------------------|
| `FINISHED`                   | `completed`      |
| `STOPPED`                    | `canceled`       |
| `AWAITING_USER_INPUT`        | `input_required` |
| `AWAITING_USER_CONFIRMATION` | `input_required` |
| `RUNNING`                    | `working`        |
| `LOADING`                    | `working`        |
| `RATE_LIMITED`               | `working`        |
| `USER_CONFIRMED`             | `working`        |
| `ERROR`                      | `failed`         |

## Starting a task

A task can be started in two ways:

- `message/send` — non-streaming mode
- `message/stream` — streaming mode

If `task_id` is not provided, the handler generates a new one

If `context_id` is missing, or if no OpenHands session exists for that context, the handler creates a new conversation
automatically.

If the task already exists and is already in a terminal state, the handler rejects further interaction with it.

---

## Non-streaming mode

`message/send` starts a task and returns the initial `Task` snapshot immediately

### Behavior:

- returns a `Task` snapshot immediately
- actual processing continues in the background
- the returned task is usually not final yet
- the client should call `tasks/get` until the task reaches a terminal state

### Example

```python
client = await ClientFactory.connect(
    agent=agent_card,
    client_config=ClientConfig(streaming=False)
)
response = None
async for event in client.send_message(
        Message(
            role=Role.user,
            message_id=uuid4().hex,
            parts=[
                Part(
                    root=TextPart(
                        text='<Instruction for task>'
                    )
                ),
            ],
            metadata={
                'openhands/agent': 'CodeActAgent',
                'openhands/show-all-events': False,
                'openhands/auto-continue': True,
            }
        )):
    response = event
)
```

---

## Streaming mode

`message/stream` starts a task and returns live updates while it is running

Behavior:

- the stream starts with a `status-update`
- if the task is already terminal, the first update is returned with `final=True`
- otherwise, the client receives live updates until the task finishes

### Example

```python
client = await ClientFactory.connect(
    agent=agent_card,
    client_config=ClientConfig(streaming=True,
                               httpx_client=httpx.AsyncClient(
                                   timeout=httpx.Timeout(
                                       connect=30.0,
                                       read=360.0,
                                       write=30.0,
                                       pool=30.0)
                               )),
)
task, update = None, None
async for event in client.send_message(Message(
        role=Role.user,
        message_id=uuid4().hex,
        parts=[
            Part(
                root=TextPart(
                    text='<Instruction for task>'
                )
            ),
        ],
        metadata={
            'openhands/agent': 'CodeActAgent',
            'openhands/show-all-events': False,
            'openhands/auto-continue': True,
        }
)):
    task, update = event
```

## Metadata

The handler uses the `openhands/*` metadata namespace

Supported metadata:

- `openhands/agent` — selects which OpenHands agent is used for a newly created conversation
- `openhands/show-all-events` — controls whether only user-visible events or the full internal event history is
  returned.
  By default `openhands/show-all-events` is False
- `openhands/auto-continue` — controls whether the handler should automatically continue execution when the agent
  requests user input. By default `openhands/auto-continue` is False

---

## Getting task status

`tasks/get` returns the current task snapshot

A task response may include:

- `id`
- `context_id`
- `status`
- `history`
- `metadata`
- `artifacts`

### Example

```python
task = await client.get_task(
    TaskQueryParams(
        id=task_id,
        history_length=10,
        metadata={
            'openhands/show-all-events': False
        }
    )
)
```

`history_length` here controls how many history entries are returned:

- `None` — all matching history
- N — last `N` entries
- `0` — empty history

---

## Resubscribing to a task

`tasks/resubscribe` restores the event stream after the original streaming connection was interrupted

Behavior:
Handler replays history events that were not streamed yet and then continues with live events

### Example

```python
task, update = None, None
async for event in client.resubscribe_task(
        TaskIdParams(
            id=task_id,
            metadata={
                'openhands/show-all-events': False
            }
        )
):
    task, update = event
```

---

## Canceling a task

`tasks/cancel` cancels an active task.

Behavior:
Task state is updated to `canceled` and handler triggers the internal stop for task

### Example

```python
task = await client.cancel_task(
    TaskIdParams(id=task_id)
)
```
