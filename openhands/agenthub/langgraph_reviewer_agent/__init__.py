"""LangGraph Reviewer Agent for code review using ReAct pattern."""

try:
    from openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent import (
        LangGraphReviewerAgent,
    )
    from openhands.controller.agent import Agent

    Agent.register('LangGraphReviewerAgent', LangGraphReviewerAgent)

    __all__ = ['LangGraphReviewerAgent']
except ImportError as e:
    # LangGraph dependencies not installed
    import warnings

    warnings.warn(
        f'LangGraph dependencies not available: {e}. '
        'Install with: pip install openhands-ai[langgraph_agent]',
        ImportWarning,
    )
    __all__ = []

