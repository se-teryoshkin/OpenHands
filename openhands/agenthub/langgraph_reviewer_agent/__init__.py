# LangGraph-based Code Review Agent (Structured workflow)

# Lazy imports to avoid loading heavy dependencies unless needed
def __getattr__(name):
    """Lazy load modules to avoid import errors if langgraph not installed."""
    if name == "ReviewAgentConfig":
        from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
        return ReviewAgentConfig

    if name in ("ReviewComment", "ReviewResult", "IdentifiedPattern", "PatternIdentificationOutput", "IssueLocation"):
        from openhands.agenthub.langgraph_reviewer_agent.models import (
            IdentifiedPattern,
            IssueLocation,
            PatternIdentificationOutput,
            ReviewComment,
            ReviewResult,
        )
        return locals()[name]

    if name == "PatternScoutAgent":
        from openhands.agenthub.langgraph_reviewer_agent.pattern_scout_agent import PatternScoutAgent
        return PatternScoutAgent

    if name == "StructuredCodeReviewAgent":
        from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent
        return StructuredCodeReviewAgent

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ReviewAgentConfig",
    "ReviewComment",
    "ReviewResult",
    "IdentifiedPattern",
    "IssueLocation",
    "PatternIdentificationOutput",
    "PatternScoutAgent",
    "StructuredCodeReviewAgent",
]
