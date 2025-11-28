from pydantic import BaseModel, Field, SecretStr


class LangfuseConfig(BaseModel):
    """Configuration for Langfuse observability."""

    enabled: bool = Field(
        default=False,
        description='Enable Langfuse monitoring when true. Requires explicit user analytics consent per session.',
    )
    host: str = Field(
        default='https://cloud.langfuse.com',
        description='Base URL for the Langfuse instance.',
    )
    public_key: SecretStr | None = Field(
        default=None, description='Langfuse public key used for authentication.'
    )
    secret_key: SecretStr | None = Field(
        default=None, description='Langfuse secret key used for authentication.'
    )
    environment: str = Field(
        default='default', description='Environment label displayed in Langfuse.'
    )
    release: str | None = Field(
        default=None, description='Optional release identifier propagated to Langfuse.'
    )
    debug: bool = Field(
        default=False,
        description='Enable verbose Langfuse client logging for troubleshooting.',
    )
    sample_rate: float = Field(
        default=1.0, ge=0.0, le=1.0, description='Sampling rate applied by Langfuse.'
    )
    default_tags: list[str] = Field(
        default_factory=list,
        description='Tags automatically added to every Langfuse trace.',
    )
    timeout_seconds: float | None = Field(
        default=None,
        description='Optional request timeout forwarded to the Langfuse client.',
    )
    flush_at: int | None = Field(
        default=None,
        description='Override Langfuse batching flush_at parameter. Uses library default when unset.',
    )
    flush_interval: float | None = Field(
        default=None,
        description='Override Langfuse batching flush_interval parameter. Uses library default when unset.',
    )

