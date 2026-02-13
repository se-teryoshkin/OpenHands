"""Configuration for the LangGraph Code Review Agent."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewAgentConfig:
    """Configuration for the Code Review Agent.

    Uses separate environment variables for the review agent's LLM:
    - GPT_OSS_HOST: The base URL for the LLM API
    - GPT_OSS_KEY: The API key
    - GPT_OSS_MODEL_NAME: The model name to use
    """

    # LLM Configuration
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("GPT_OSS_HOST", "https://api.openai.com/v1")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("GPT_OSS_KEY", os.getenv("OPENAI_API_KEY", ""))
    )
    llm_model_name: str = field(
        default_factory=lambda: os.getenv("GPT_OSS_MODEL_NAME", "gpt-4o")
    )

    # Agent behavior
    temperature: float = 1.0
    top_p = 1
    max_iterations: int = 25  # Each iteration = ~3 LangGraph steps
    verbose: bool = True

    # Review settings
    strict_mode: bool = False  # If True, fails on warnings too
    include_suggestions: bool = True

    # Tool settings
    enable_signature_validation: bool = True
    enable_field_validation: bool = True
    enable_test_validation: bool = True
    enable_structure_validation: bool = True

    # Langfuse tracing (optional)
    langfuse_public_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    langfuse_secret_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    langfuse_host: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    )
    langfuse_trace_name: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_TRACE_NAME", "langgraph-code-review")
    )
    langfuse_session_id: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SESSION_ID", "")
    )
    langfuse_enabled: bool = field(
        default_factory=lambda: os.getenv("REVIEW_AGENT_LANGFUSE_ENABLED", "").lower() in {"1", "true", "yes"}
        or (
            bool(os.getenv("LANGFUSE_PUBLIC_KEY"))
            and bool(os.getenv("LANGFUSE_SECRET_KEY"))
        )
    )

    def validate(self) -> None:
        """Validate the configuration."""
        if not self.llm_api_key:
            raise ValueError(
                "LLM API key not configured. Set GPT_OSS_KEY or OPENAI_API_KEY environment variable."
            )
        if self.langfuse_enabled and (
            not self.langfuse_public_key or not self.langfuse_secret_key
        ):
            raise ValueError(
                "Langfuse tracing is enabled but keys are missing. "
                "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, or disable with REVIEW_AGENT_LANGFUSE_ENABLED=false."
            )

    @classmethod
    def from_env(cls) -> "ReviewAgentConfig":
        """Create configuration from environment variables."""
        return cls()
