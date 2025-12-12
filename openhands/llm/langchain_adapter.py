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
            # Handle tool calls if present - convert from LangChain to OpenAI format
            if hasattr(message, 'tool_calls') and message.tool_calls:
                import json
                openai_tool_calls = []
                for tc in message.tool_calls:
                    # LangChain format: {name, args, id, type: 'tool_call'}
                    # OpenAI format: {id, type: 'function', function: {name, arguments}}
                    tool_call = {
                        'id': tc.get('id', ''),
                        'type': 'function',
                        'function': {
                            'name': tc.get('name', ''),
                            'arguments': json.dumps(tc.get('args', {}))
                        }
                    }
                    openai_tool_calls.append(tool_call)
                result['tool_calls'] = openai_tool_calls
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
            logger.warning('No choices in response!')
            return AIMessage(content='')

        choice = response.choices[0]
        message = choice.message

        content = message.content if hasattr(message, 'content') else ''

        # Handle reasoning_content for models like gpt-oss that use it
        if not content and hasattr(message, 'reasoning_content') and message.reasoning_content:
            logger.info('Using reasoning_content as content')
            content = message.reasoning_content

        # Handle tool calls if present
        tool_calls = []
        if hasattr(message, 'tool_calls') and message.tool_calls:
            logger.info(f'Found {len(message.tool_calls)} tool calls')
            # Convert LiteLLM tool calls to LangChain format
            import json
            for tc in message.tool_calls:
                # Get function info from LiteLLM format
                if hasattr(tc, 'function'):
                    func = tc.function
                    func_name = getattr(func, 'name', '')
                    func_args_str = getattr(func, 'arguments', '{}')

                    # Parse arguments from JSON string to dict
                    try:
                        func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                    except json.JSONDecodeError:
                        logger.warning(f'Failed to parse tool arguments: {func_args_str}')
                        func_args = {}

                    # Convert to LangChain format: {name, args, id, type}
                    tool_calls.append({
                        'name': func_name,
                        'args': func_args,
                        'id': getattr(tc, 'id', ''),
                        'type': 'tool_call'
                    })
                else:
                    logger.warning(f'Tool call missing function attribute: {tc}')
        else:
            # For models that don't support native tool calling, check if finish_reason suggests tool use
            if choice.finish_reason == 'stop' and not content:
                logger.warning('Model completed without tool calls or content - may need different configuration')

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

        # Add tools if bound
        completion_kwargs = kwargs.copy()
        if hasattr(self, '_bound_tools') and self._bound_tools:
            completion_kwargs['tools'] = self._bound_tools
            # Enable automatic function calling
            if 'tool_choice' not in completion_kwargs:
                completion_kwargs['tool_choice'] = 'auto'

        # Call the OpenHands LLM
        try:
            response = self.llm.completion(messages=openhands_messages, **completion_kwargs)
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

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> 'OpenHandsLangChainAdapter':
        """Bind tools to the model for function calling.

        Args:
            tools: List of tools to bind
            **kwargs: Additional arguments

        Returns:
            Self (for chaining)
        """
        # Convert tools to JSON schema format for the LLM
        if not hasattr(self, '_bound_tools'):
            self._bound_tools = []

        # Convert langchain tools to OpenAI tool format
        converted_tools = []
        for tool in tools:
            if hasattr(tool, 'get_input_schema'):
                # LangChain structured tool
                schema_cls = tool.get_input_schema()
                schema = schema_cls.model_json_schema()
                converted_tools.append({
                    'type': 'function',
                    'function': {
                        'name': tool.name,
                        'description': tool.description or '',
                        'parameters': schema
                    }
                })
            else:
                # Already in dict format
                converted_tools.append(tool)

        self._bound_tools = converted_tools
        return self

