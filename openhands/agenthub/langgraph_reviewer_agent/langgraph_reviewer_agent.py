"""LangGraph-based code reviewer agent using ReAct pattern."""

import os
from typing import TYPE_CHECKING, Any, TypedDict

from openhands.controller.agent import Agent
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    AgentFinishAction,
    CmdRunAction,
    FileReadAction,
    MessageAction,
)
from openhands.llm.llm_registry import LLMRegistry
from openhands.utils.prompt import PromptManager

if TYPE_CHECKING:
    pass

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import END, StateGraph
    from langgraph.prebuilt import create_react_agent

    from openhands.agenthub.langgraph_reviewer_agent.tools import create_reviewer_tools
    from openhands.llm.langchain_adapter import OpenHandsLangChainAdapter

    LANGGRAPH_AVAILABLE = True
except ImportError as e:
    logger.warning(f'LangGraph not available: {e}')
    LANGGRAPH_AVAILABLE = False


class ReviewerState(TypedDict):
    """State for the reviewer agent."""

    messages: list[Any]
    next_action: dict[str, Any] | None
    review_context: dict[str, Any]


class LangGraphReviewerAgent(Agent):
    """Code reviewer agent using LangGraph and ReAct pattern.

    This agent reviews code changes, runs tests, and provides feedback
    using a ReAct (Reasoning and Acting) approach powered by LangGraph.
    """

    VERSION = '1.0'

    def __init__(self, config: AgentConfig, llm_registry: LLMRegistry) -> None:
        """Initialize the LangGraph reviewer agent.

        Args:
            config: Agent configuration
            llm_registry: LLM registry for accessing language models
        """
        super().__init__(config, llm_registry)

        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                'LangGraph dependencies not installed. '
                'Install with: pip install openhands-ai[langgraph_agent]'
            )

        # Initialize the LangChain adapter for OpenHands LLM
        self.langchain_llm = OpenHandsLangChainAdapter(llm=self.llm)

        # Create tools for the agent
        self.reviewer_tools = create_reviewer_tools()

        # Initialize the graph (will be built on first use)
        self._graph = None
        self._graph_state: ReviewerState | None = None

        logger.info(
            f'Initialized LangGraphReviewerAgent with {len(self.reviewer_tools)} tools'
        )

    @property
    def prompt_manager(self) -> PromptManager:
        """Get the prompt manager for the agent.

        Returns:
            PromptManager instance
        """
        if self._prompt_manager is None:
            self._prompt_manager = PromptManager(
                prompt_dir=os.path.join(os.path.dirname(__file__), 'prompts'),
                system_prompt_filename='system_prompt.j2',
            )
        return self._prompt_manager

    def _build_graph(self) -> Any:
        """Build the LangGraph graph for the reviewer agent.

        Returns:
            Compiled LangGraph graph
        """
        # Create a simple ReAct agent using LangGraph
        agent_executor = create_react_agent(
            self.langchain_llm,
            self.reviewer_tools,
        )

        return agent_executor

    def _initialize_state(self, state: State) -> ReviewerState:
        """Initialize the reviewer state from OpenHands state.

        Args:
            state: OpenHands state

        Returns:
            Reviewer state for LangGraph
        """
        # Get the system prompt
        system_prompt = self.prompt_manager.get_system_message()

        # Get context from state inputs (provided by delegating agent)
        task = state.inputs.get('task', 'Review the code in this repository')
        focus = state.inputs.get('focus', '')
        run_tests = state.inputs.get('run_tests', True)

        # Build initial message
        initial_message = f"""Task: {task}

Please review the code in this repository. Focus areas: {focus if focus else 'general code review'}.
Should run tests: {run_tests}

Start by examining what changes were made (use git diff or git status if available),
then analyze the code, run tests if requested, and provide your findings.
"""

        return ReviewerState(
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=initial_message),
            ],
            next_action=None,
            review_context={
                'task': task,
                'focus': focus,
                'run_tests': run_tests,
            },
        )

    def step(self, state: State) -> Action:
        """Perform one step of the reviewer agent.

        Args:
            state: Current OpenHands state

        Returns:
            Action to execute
        """
        # Initialize graph if needed
        if self._graph is None:
            self._graph = self._build_graph()

        # Initialize state if needed
        if self._graph_state is None:
            self._graph_state = self._initialize_state(state)
            logger.info('Initialized reviewer state')

        # If we have a pending action from last step, check if we got the observation
        if self._graph_state['next_action'] is not None:
            # Look for the most recent observation in state history
            if state.history:
                last_event = state.history[-1]
                # Add the observation to messages
                observation_text = f"Observation: {getattr(last_event, 'content', str(last_event))}"
                self._graph_state['messages'].append(
                    HumanMessage(content=observation_text)
                )
                self._graph_state['next_action'] = None

        try:
            # Invoke the agent
            result = self._graph.invoke(
                {'messages': self._graph_state['messages']},
                {'recursion_limit': 10},
            )

            # Extract the response
            response_messages = result.get('messages', [])

            if not response_messages:
                return MessageAction(
                    content='No response from reviewer agent', wait_for_response=False
                )

            # Get the last message
            last_message = response_messages[-1]

            # Update state messages
            self._graph_state['messages'] = response_messages

            # Check if this is a tool call
            if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
                tool_call = last_message.tool_calls[0]
                tool_name = tool_call.get('name', '')
                tool_args = tool_call.get('args', {})

                logger.info(f'Tool call: {tool_name} with args: {tool_args}')

                # Handle different tool calls
                if tool_name == 'run_command':
                    command = tool_args.get('command', '')
                    self._graph_state['next_action'] = {'type': 'command', 'command': command}
                    return CmdRunAction(command=command)

                elif tool_name == 'read_file':
                    path = tool_args.get('path', '')
                    self._graph_state['next_action'] = {'type': 'read_file', 'path': path}
                    return FileReadAction(path=path)

                elif tool_name == 'finish_review':
                    # Extract review findings
                    summary = tool_args.get('summary', '')
                    critical_issues = tool_args.get('critical_issues', [])
                    major_issues = tool_args.get('major_issues', [])
                    minor_issues = tool_args.get('minor_issues', [])
                    test_results = tool_args.get('test_results', '')
                    recommendation = tool_args.get('recommendation', 'APPROVE')

                    # Format the review output
                    review_output = self._format_review_output(
                        summary=summary,
                        critical_issues=critical_issues,
                        major_issues=major_issues,
                        minor_issues=minor_issues,
                        test_results=test_results,
                        recommendation=recommendation,
                    )

                    return AgentFinishAction(
                        outputs={
                            'summary': summary,
                            'critical_issues': critical_issues,
                            'major_issues': major_issues,
                            'minor_issues': minor_issues,
                            'test_results': test_results,
                            'recommendation': recommendation,
                            'review': review_output,
                        }
                    )

            # If no tool call, return the message content
            content = getattr(last_message, 'content', '')
            if content:
                return MessageAction(content=content, wait_for_response=False)

            # Default: finish without specific findings
            return AgentFinishAction(
                outputs={'summary': 'Review completed', 'recommendation': 'APPROVE'}
            )

        except Exception as e:
            logger.error(f'Error in reviewer agent step: {e}', exc_info=True)
            return AgentFinishAction(
                outputs={
                    'summary': f'Review failed with error: {str(e)}',
                    'recommendation': 'ERROR',
                }
            )

    def _format_review_output(
        self,
        summary: str,
        critical_issues: list[str],
        major_issues: list[str],
        minor_issues: list[str],
        test_results: str,
        recommendation: str,
    ) -> str:
        """Format the review output.

        Args:
            summary: Review summary
            critical_issues: List of critical issues
            major_issues: List of major issues
            minor_issues: List of minor issues
            test_results: Test results
            recommendation: Final recommendation

        Returns:
            Formatted review output
        """
        output = f"""# Code Review Summary

{summary}

## Critical Issues
{chr(10).join(f'- {issue}' for issue in critical_issues) if critical_issues else 'None'}

## Major Issues
{chr(10).join(f'- {issue}' for issue in major_issues) if major_issues else 'None'}

## Minor Issues
{chr(10).join(f'- {issue}' for issue in minor_issues) if minor_issues else 'None'}

## Test Results
{test_results if test_results else 'No tests run'}

## Recommendation
**{recommendation}**
"""
        return output

    def reset(self) -> None:
        """Reset the agent state."""
        super().reset()
        self._graph_state = None
        logger.info('Reset reviewer agent state')

