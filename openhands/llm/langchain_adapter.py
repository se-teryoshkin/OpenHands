"""LangChain adapter for OpenHands LLM.

This module provides an adapter that allows OpenHands LLM instances
to be used with LangChain and LangGraph.
"""

from typing import Any, Iterator, Optional

try:
    from langchain_core.callbacks.manager import CallbackManagerForLLMRun
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
    from langchain_core.outputs import ChatGeneration, ChatResult

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    BaseChatModel = object  # type: ignore
    BaseMessage = object  # type: ignore

from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message as OpenHandsMessage
from openhands.llm.llm import LLM


class OpenHandsLangChainAdapter(BaseChatModel):
    """Adapter to use OpenHands LLM with LangChain.

    This adapter wraps an OpenHands LLM instance and provides the interface
    expected by LangChain's BaseChatModel, allowing it to be used with
    LangChain agents and LangGraph.
    """

    llm: Any  # OpenHands LLM instance
    """The underlying OpenHands LLM instance."""

    def __init__(self, llm: LLM, **kwargs: Any):
        """Initialize the adapter with an OpenHands LLM instance.

        Args:
            llm: The OpenHands LLM instance to wrap
            **kwargs: Additional arguments passed to BaseChatModel
        """
        super().__init__(llm=llm, **kwargs)

    @property
    def _llm_type(self) -> str:
        """Return the type of LLM."""
        return 'openhands_llm'

    def _convert_message_to_openhands(self, message: BaseMessage) -> dict[str, Any]:
        """Convert a LangChain message to OpenHands message format.

        Args:
            message: LangChain message to convert

        Returns:
            Dictionary in OpenHands message format
        """
        if isinstance(message, SystemMessage):
            return {'role': 'system', 'content': message.content}
        elif isinstance(message, HumanMessage):
            return {'role': 'user', 'content': message.content}
        elif isinstance(message, AIMessage):
            result: dict[str, Any] = {'role': 'assistant', 'content': message.content}
            # Handle tool calls if present
            if hasattr(message, 'tool_calls') and message.tool_calls:
                result['tool_calls'] = message.tool_calls
            return result
        elif isinstance(message, ToolMessage):
            return {
                'role': 'tool',
                'content': message.content,
                'tool_call_id': message.tool_call_id,
            }
        else:
            # Default to user message
            return {'role': 'user', 'content': str(message.content)}

    def _convert_openhands_to_message(self, response: Any) -> AIMessage:
        """Convert OpenHands LLM response to LangChain message.

        Args:
            response: OpenHands ModelResponse

        Returns:
            LangChain AIMessage
        """
        if not response.choices or len(response.choices) == 0:
            return AIMessage(content='')

        choice = response.choices[0]
        message = choice.message

        content = message.content if hasattr(message, 'content') else ''

        # Handle tool calls if present
        tool_calls = []
        if hasattr(message, 'tool_calls') and message.tool_calls:
            tool_calls = message.tool_calls

        return AIMessage(content=content or '', tool_calls=tool_calls)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a response using the OpenHands LLM.

        Args:
            messages: List of LangChain messages
            stop: Optional list of stop sequences
            run_manager: Optional callback manager
            **kwargs: Additional arguments

        Returns:
            ChatResult with the generated response
        """
        # Convert LangChain messages to OpenHands format
        openhands_messages = [
            self._convert_message_to_openhands(msg) for msg in messages
        ]

        # Call the OpenHands LLM
        try:
            response = self.llm.completion(messages=openhands_messages, **kwargs)
        except Exception as e:
            logger.error(f'Error calling OpenHands LLM: {e}')
            raise

        # Convert response back to LangChain format
        ai_message = self._convert_openhands_to_message(response)

        generation = ChatGeneration(message=ai_message)
        return ChatResult(generations=[generation])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGeneration]:
        """Stream responses (not implemented for OpenHands LLM).

        Args:
            messages: List of LangChain messages
            stop: Optional list of stop sequences
            run_manager: Optional callback manager
            **kwargs: Additional arguments

        Yields:
            ChatGeneration chunks (not implemented)
        """
        # OpenHands LLM doesn't support streaming in this adapter
        # Fall back to regular generation
        result = self._generate(messages, stop, run_manager, **kwargs)
        yield result.generations[0]

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """Return identifying parameters."""
        return {
            'model_name': self.llm.config.model if hasattr(self.llm, 'config') else 'unknown',
        }

