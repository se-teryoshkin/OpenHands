"""Unit tests for LangGraph Reviewer Agent."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from openhands.controller.state.state import State
from openhands.core.config import AgentConfig, LLMConfig
from openhands.core.schema import AgentState
from openhands.events.action import AgentFinishAction, CmdRunAction, FileReadAction
from openhands.llm.llm_registry import LLMRegistry
from openhands.llm.metrics import Metrics

# Check if LangGraph is available
try:
    from openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent import (
        LangGraphReviewerAgent,
    )

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not LANGGRAPH_AVAILABLE, reason='LangGraph dependencies not installed'
)


@pytest.fixture
def mock_llm():
    """Create a mock LLM instance."""
    llm = MagicMock()
    llm.config = MagicConfig(model='gpt-4')
    llm.completion = MagicMock()
    return llm


@pytest.fixture
def agent_config():
    """Create agent configuration."""
    return AgentConfig()


@pytest.fixture
def llm_config():
    """Create LLM configuration."""
    return LLMConfig(model='gpt-4')


@pytest.fixture
def llm_registry(llm_config):
    """Create LLM registry."""
    metrics = Metrics()
    return LLMRegistry({'llm': llm_config}, 'llm', metrics)


@pytest.fixture
def mock_state():
    """Create a mock state."""
    state = State(
        session_id='test-session',
        agent_state=AgentState.RUNNING,
        inputs={
            'task': 'Review this code',
            'focus': 'security',
            'run_tests': True,
        },
    )
    return state


class TestLangGraphReviewerAgent:
    """Test cases for LangGraphReviewerAgent."""

    def test_agent_initialization(self, agent_config, llm_registry):
        """Test that the agent initializes correctly."""
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        assert agent is not None
        assert agent.VERSION == '1.0'
        assert agent.reviewer_tools is not None
        assert len(agent.reviewer_tools) > 0

    def test_agent_has_prompt_manager(self, agent_config, llm_registry):
        """Test that the agent has a prompt manager."""
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        prompt_manager = agent.prompt_manager
        assert prompt_manager is not None

        # Check that system prompt exists
        system_prompt = prompt_manager.get_system_message()
        assert system_prompt is not None
        assert len(system_prompt) > 0

    @patch('openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent.create_react_agent')
    def test_agent_step_initializes_graph(
        self, mock_create_agent, agent_config, llm_registry, mock_state
    ):
        """Test that calling step initializes the graph."""
        # Setup mock
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {'messages': []}
        mock_create_agent.return_value = mock_graph

        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        # First step should initialize the graph
        action = agent.step(mock_state)

        assert agent._graph is not None
        assert agent._graph_state is not None
        mock_create_agent.assert_called_once()

    @patch('openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent.create_react_agent')
    def test_agent_step_returns_action(
        self, mock_create_agent, agent_config, llm_registry, mock_state
    ):
        """Test that step returns an action."""
        # Setup mock to return a message
        mock_message = MagicMock()
        mock_message.content = 'Test response'
        mock_message.tool_calls = []

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {'messages': [mock_message]}
        mock_create_agent.return_value = mock_graph

        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        action = agent.step(mock_state)

        assert action is not None

    def test_agent_reset(self, agent_config, llm_registry):
        """Test that reset clears the agent state."""
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        # Set some state
        agent._graph_state = {'messages': []}

        # Reset
        agent.reset()

        assert agent._graph_state is None

    @patch('openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent.create_react_agent')
    def test_agent_handles_run_command_tool(
        self, mock_create_agent, agent_config, llm_registry, mock_state
    ):
        """Test that agent handles run_command tool call."""
        # Setup mock to return a tool call
        mock_message = MagicMock()
        mock_message.tool_calls = [
            {
                'name': 'run_command',
                'args': {'command': 'pytest'},
            }
        ]

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {'messages': [mock_message]}
        mock_create_agent.return_value = mock_graph

        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        action = agent.step(mock_state)

        assert isinstance(action, CmdRunAction)
        assert action.command == 'pytest'

    @patch('openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent.create_react_agent')
    def test_agent_handles_read_file_tool(
        self, mock_create_agent, agent_config, llm_registry, mock_state
    ):
        """Test that agent handles read_file tool call."""
        # Setup mock to return a tool call
        mock_message = MagicMock()
        mock_message.tool_calls = [
            {
                'name': 'read_file',
                'args': {'path': 'test.py'},
            }
        ]

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {'messages': [mock_message]}
        mock_create_agent.return_value = mock_graph

        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        action = agent.step(mock_state)

        assert isinstance(action, FileReadAction)
        assert action.path == 'test.py'

    @patch('openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent.create_react_agent')
    def test_agent_handles_finish_review_tool(
        self, mock_create_agent, agent_config, llm_registry, mock_state
    ):
        """Test that agent handles finish_review tool call."""
        # Setup mock to return a tool call
        mock_message = MagicMock()
        mock_message.tool_calls = [
            {
                'name': 'finish_review',
                'args': {
                    'summary': 'Review completed',
                    'critical_issues': [],
                    'major_issues': ['Issue 1'],
                    'minor_issues': [],
                    'test_results': 'All tests passed',
                    'recommendation': 'REQUEST_CHANGES',
                },
            }
        ]

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {'messages': [mock_message]}
        mock_create_agent.return_value = mock_graph

        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        action = agent.step(mock_state)

        assert isinstance(action, AgentFinishAction)
        assert action.outputs['summary'] == 'Review completed'
        assert action.outputs['recommendation'] == 'REQUEST_CHANGES'
        assert len(action.outputs['major_issues']) == 1

    def test_format_review_output(self, agent_config, llm_registry):
        """Test review output formatting."""
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        output = agent._format_review_output(
            summary='Test review',
            critical_issues=['Critical 1'],
            major_issues=['Major 1', 'Major 2'],
            minor_issues=[],
            test_results='All passed',
            recommendation='APPROVE',
        )

        assert 'Test review' in output
        assert 'Critical 1' in output
        assert 'Major 1' in output
        assert 'Major 2' in output
        assert 'All passed' in output
        assert 'APPROVE' in output


class MagicConfig:
    """Mock configuration class."""

    def __init__(self, model: str):
        self.model = model


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv('OPENAI_API_KEY'), reason='OPENAI_API_KEY not set'
)
class TestLangGraphReviewerAgentIntegration:
    """Integration tests for LangGraph Reviewer Agent (requires API key)."""

    def test_agent_full_review_cycle(self, agent_config, llm_registry, tmp_path):
        """Test a full review cycle with a temporary code directory."""
        # Create a simple test file
        test_file = tmp_path / 'test.py'
        test_file.write_text(
            """
def add(a, b):
    return a + b

def test_add():
    assert add(1, 2) == 3
"""
        )

        # Create state
        state = State(
            session_id='integration-test',
            agent_state=AgentState.RUNNING,
            inputs={
                'task': f'Review code in {tmp_path}',
                'focus': '',
                'run_tests': False,  # Don't actually run tests in integration test
            },
        )

        # Create agent
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        # Run a few steps
        max_steps = 5
        for i in range(max_steps):
            action = agent.step(state)

            if isinstance(action, AgentFinishAction):
                # Review completed
                assert 'summary' in action.outputs
                break

            # Add mock observation for next step
            from openhands.events.observation import CmdOutputObservation

            state.history.append(
                CmdOutputObservation(
                    command='mock',
                    exit_code=0,
                    content='Mock observation',
                )
            )

