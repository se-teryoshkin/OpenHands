import asyncio
import os
from copy import copy
from uuid import uuid4
from a2a.client import ClientConfig, ClientFactory, ClientEvent
from a2a.client.base_client import BaseClient
from a2a.types import Message, Role, TextPart, AgentCard, Task, TaskState, TaskQueryParams
from typing import Optional, Literal, Any, Callable, TYPE_CHECKING
import httpx
import logging
from pydantic import BaseModel
import traceback


if TYPE_CHECKING:
    from langchain_core.tools import BaseTool as LangchainTool


logger = logging.getLogger(__name__)


DEFAULT_HTTPX_CLIENT = httpx.AsyncClient(timeout=int(os.environ.get("HTTPX_TIMEOUT", 120)))
TASK_TERMINAL_STATES = {TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected}


class RemoteA2AAgentResponse(BaseModel):
    status: Literal["success", "error"]
    agent_id: str
    message_id: str
    response: Optional[dict] = None
    error: Optional[str] = None


class RemoteA2AAgent:

    def __init__(
            self,
            well_known_url: str,
            agent_id: Optional[str] = None,
            config: Optional[ClientConfig] = None,
            default_metadata: Optional[dict[str, Any]] = None,
            events_callback: Optional[Callable] = None,
    ):
        if agent_id is None:
            agent_id = uuid4().hex

        if default_metadata is not None:
            metadata = copy(default_metadata)
        else:
            metadata: dict[str, Any] = dict()

        if config is None:
            config = ClientConfig()

        if config.httpx_client is None:
            config.httpx_client = DEFAULT_HTTPX_CLIENT

        self.well_known_url = well_known_url
        self.agent_id = agent_id
        self.config = config
        self.metadata = metadata

        self._is_initialized = False
        self._client: Optional[BaseClient] = None

        self._events_callback = events_callback

    async def get_agent_info(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_details": await self.get_agent_card()
        }

    async def get_agent_card(self) -> AgentCard:
        if not self._is_initialized:
            await self._initialize()
        return self._client._card

    async def _initialize(self):
        if self._is_initialized:
            return

        # JSON-RPC Client
        self._client: BaseClient = await ClientFactory.connect(
            agent=self.well_known_url, client_config=self.config,
        )
        self._is_initialized = True

    async def send_message(
            self,
            message_text: str | None = None,
            message_id: str | None = None,
            message: Message | None = None
    ) -> RemoteA2AAgentResponse:

        if message_text is None and message is None:
            raise ValueError("Message object or message_text is required")

        if message_text is not None and message_id is None:
            raise ValueError("message_id is required if message_text is passed")

        if message is None:
            message = Message(
                message_id=message_id,
                role=Role.user,
                parts=[TextPart(text=message_text)],
            )

        if not self._is_initialized:
            await self._initialize()

        logger.info(f"Sending message to agent {self.agent_id} ({self.well_known_url}) - Message: {message}")

        event: ClientEvent | Message
        async for event in self._client.send_message(message, request_metadata=copy(self.metadata)):
            if isinstance(event, Message):
                event: Message

                return RemoteA2AAgentResponse(
                    status="success",
                    agent_id=self.agent_id,
                    message_id=message.message_id,
                    response=event.model_dump(mode="python", exclude_none=True),
                )

            elif isinstance(event, tuple) and len(event) == 2:
                event: ClientEvent
                task, update_event = event

                if self.config.streaming:
                    # TODO: Add streaming
                    raise NotImplementedError()
                else:

                    if self.config.polling:
                        task = await self.poll_task_until_finished(task)

                    return RemoteA2AAgentResponse(
                        status="success",
                        agent_id=self.agent_id,
                        message_id=message.message_id,
                        response={
                            "task": task.model_dump(mode="python", exclude_none=True),
                            "update_event": (
                                update_event.model_dump(mode="python", exclude_none=True)
                                if update_event is not None
                                else None
                            ),
                        }
                    )

            else:
                raise RuntimeError(f"Unexpected response type: {type(event)}")

        return RemoteA2AAgentResponse(
            status="error",
            agent_id=self.agent_id,
            message_id=message.message_id,
            error="No response received from agent.",
        )

    async def get_task(
            self,
            task_id: str,
            metadata: Optional[dict[str, Any]] = None,
            history_length: Optional[int] = None
    ) -> Task:

        if not self._is_initialized:
            await self._initialize()

        if metadata is None:
            metadata = copy(self.metadata)

        task: Task = await self._client.get_task(request=TaskQueryParams(
            id=task_id,
            metadata=metadata,  # noqa
            history_length=history_length,
        ))

        return task

    async def cancel_task(self, task: str | Task):
        raise NotImplementedError()

    async def poll_task_until_finished(self, task: Task) -> Task:

        client = self._client
        callback = self._events_callback

        task_query_params = TaskQueryParams(
            # Чтобы при поллинге минимизировать размер ответа
            history_length=0 if callback is None else None,
            id=task.id,
            metadata=copy(self.metadata),
        )

        processed_events = set()

        while True:
            # TODO: Добавить таймаут и/или количество попыток

            state = task.status.state
            if state in TASK_TERMINAL_STATES:
                break

            logger.debug(f"Task {task.id} state is {state}: {task}")

            # TODO: Добавить настройку интервала
            await asyncio.sleep(5)

            # TODO: Добавить обработку исключений
            task = await client.get_task(request=task_query_params)

            if callback is not None:
                history = task.history
                if history is not None and len(history) > 0:
                    for event_message in history:
                        if event_message.message_id not in processed_events:
                            callback(event_message)
                            processed_events.add(event_message.message_id)

        if callback is None:
            # Теперь запрашиваем ещё раз, но уже с полной историей
            task_query_params.history_length = None
            return await client.get_task(request=task_query_params)
        else:
            return task


class A2AHub:

    def __init__(
            self,
            agents: Optional[list[RemoteA2AAgent]] = None,
    ):

        if agents is None:
            agents = list()

        self.agents: dict[str, RemoteA2AAgent] = {
            agent.agent_id: agent
            for agent in agents
        }

    def get_langchain_tools(self) -> list["LangchainTool"]:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(coroutine=tool_func)
            for tool_func in (self.list_agents, self.send_message)
        ]

    # tool
    async def send_message(self, message_text: str, target_agent_id: str) -> dict[str, Any]:
        """
        Send a message to a specific A2A agent and return the response.

        Args:
            message_text: The message content to send to the agent
            target_agent_id: The ID of the target A2A agent

        Returns:
            dict: Response data including:
                - status: Whether the message was sent successfully or failed
                - response: The agent's response data (if successful)
                - error: Error message (if failed)
                - message_id: The message unique ID used
                - target_agent_id: The agent ID that was contacted
        """

        message_id = uuid4().hex

        if target_agent_id not in self.agents:
            response = RemoteA2AAgentResponse(
                status="error",
                agent_id=target_agent_id,
                message_id=message_id,
                error=f"Agent {target_agent_id} not found.",
            )
        else:
            try:
                response = await self.agents[target_agent_id].send_message(message_text, message_id)
            except Exception as e:
                response = RemoteA2AAgentResponse(
                    status="error",
                    agent_id=target_agent_id,
                    message_id=message_id,
                    error=traceback.format_exc(),
                )

        return response.model_dump(mode="python", exclude_none=True)

    # tool
    async def list_agents(self) -> dict[str, Any]:
        """
        List all discovered A2A agents and their capabilities.

        Returns:
            dict: Information about all discovered agents including:
                - success: Whether the operation succeeded
                - agents: List of discovered agents with their details
                - total_count: Total number of discovered agents
        """

        # TODO: Добавить возможность фильтровать агентов в зависимости от потребностей определённого агента
        try:
            return await self._list_agents()
        except Exception as e:
            return {
                "status": "error",
                "error": traceback.format_exc(),
                "total_count": 0,
            }

    async def _list_agents(self):

        result = list()
        if len(self.agents) > 0:

            result = await asyncio.gather(*[
                agent.get_agent_info()
                for agent in self.agents.values()
            ])

        return {
            "status": "success",
            "agents": result,
            "total_count": len(result),
        }
