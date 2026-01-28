# LangGraph-based Code Review Agent
# This agent uses LangGraph's create_react_agent for structured code review

# Lazy imports to avoid loading heavy dependencies unless needed
def __getattr__(name):
    """Lazy load modules to avoid import errors if langgraph not installed."""
    if name in ("CodeReviewAgent", "create_review_agent", "run_review"):
        from openhands.agenthub.langgraph_reviewer_agent.agent import (
            CodeReviewAgent,
            create_review_agent,
            run_review,
        )
        return locals()[name]

    if name == "ReviewAgentConfig":
        from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
        return ReviewAgentConfig

    if name in ("ReviewComment", "ReviewResult"):
        from openhands.agenthub.langgraph_reviewer_agent.models import (
            ReviewComment,
            ReviewResult,
        )
        return locals()[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CodeReviewAgent",
    "create_review_agent",
    "run_review",
    "ReviewAgentConfig",
    "ReviewComment",
    "ReviewResult",
]
