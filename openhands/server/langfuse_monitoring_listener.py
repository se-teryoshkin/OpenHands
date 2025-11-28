from __future__ import annotations

import threading
from typing import Any

from langfuse import Langfuse
from langfuse.types import TraceContext

from openhands.core.config.langfuse_config import LangfuseConfig
from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.events.event import Event
from openhands.events.serialization.event import event_to_dict
from openhands.server.monitoring import MonitoringListener


class LangfuseMonitoringListener(MonitoringListener):
    """Monitoring listener that forwards conversation activity to Langfuse."""

    def __init__(self, client: Langfuse, cfg: LangfuseConfig):
        self._client = client
        self._cfg = cfg
        self._trace_contexts: dict[str, TraceContext] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: OpenHandsConfig) -> MonitoringListener:
        langfuse_cfg = config.langfuse
        if not langfuse_cfg.enabled:
            return MonitoringListener()

        if langfuse_cfg.public_key is None or langfuse_cfg.secret_key is None:
            logger.warning(
                'Langfuse monitoring is enabled but public/secret keys are not configured. Disabling monitoring.'
            )
            return MonitoringListener()

        kwargs: dict[str, Any] = {
            'public_key': langfuse_cfg.public_key.get_secret_value(),
            'secret_key': langfuse_cfg.secret_key.get_secret_value(),
            'host': langfuse_cfg.host,
            'environment': langfuse_cfg.environment,
            'release': langfuse_cfg.release,
            'debug': langfuse_cfg.debug,
            'sample_rate': langfuse_cfg.sample_rate,
        }
        if langfuse_cfg.timeout_seconds is not None:
            kwargs['timeout'] = int(langfuse_cfg.timeout_seconds)
        if langfuse_cfg.flush_at is not None:
            kwargs['flush_at'] = langfuse_cfg.flush_at
        if langfuse_cfg.flush_interval is not None:
            kwargs['flush_interval'] = langfuse_cfg.flush_interval

        try:
            client = Langfuse(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                'Failed to initialize Langfuse client, analytics will be disabled. Error: %s',
                exc,
            )
            return MonitoringListener()

        return cls(client, langfuse_cfg)

    def on_session_event(self, conversation_id: str, event: Event) -> None:
        context = self._get_trace_context(conversation_id)
        payload = self._serialize_event(event)
        metadata: dict[str, Any] = {
            'conversation_id': conversation_id,
            'event_type': event.__class__.__name__,
            'source': event.source.value if event.source else None,
        }

        metrics = getattr(event, 'llm_metrics', None)
        if metrics:
            try:
                metadata['llm_metrics'] = metrics.get()
            except Exception:
                # Gracefully ignore serialization errors
                metadata['llm_metrics'] = None

        self._record_event(
            context=context,
            name=metadata['event_type'],
            payload=payload,
            metadata=metadata,
        )

    def on_agent_session_start(
        self,
        success: bool,
        duration: float,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if conversation_id is None:
            return

        context = self._get_trace_context(conversation_id)
        metadata = {
            'conversation_id': conversation_id,
            'duration_seconds': duration,
            'success': success,
            'user_id': user_id,
        }
        self._record_event(
            context=context,
            name='agent_session_start',
            payload=metadata,
            metadata=metadata,
        )

    def on_create_conversation(
        self, conversation_id: str | None = None, user_id: str | None = None
    ) -> None:
        if conversation_id is None:
            return
        context = self._get_trace_context(conversation_id)
        metadata = {
            'conversation_id': conversation_id,
            'user_id': user_id,
        }
        self._record_event(
            context=context,
            name='conversation_created',
            payload=metadata,
            metadata=metadata,
        )

    def _get_trace_context(self, conversation_id: str) -> TraceContext:
        with self._lock:
            if conversation_id in self._trace_contexts:
                return self._trace_contexts[conversation_id]

            trace_id = Langfuse.create_trace_id(seed=conversation_id)
            context: TraceContext = {'trace_id': trace_id}
            self._trace_contexts[conversation_id] = context
            return context

    def _record_event(
        self,
        *,
        context: TraceContext,
        name: str,
        payload: Any,
        metadata: dict[str, Any],
    ) -> None:
        metadata = {
            **metadata,
            'tags': list(self._cfg.default_tags),
        }
        try:
            self._client.create_event(
                trace_context=context,
                name=name,
                input=payload,
                metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning('Failed to send event to Langfuse: %s', exc)

    def _serialize_event(self, event: Event) -> Any:
        try:
            return event_to_dict(event)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning('Failed to serialize event for Langfuse: %s', exc)
            return {
                'message': event.message,
                'event_type': event.__class__.__name__,
                'error': str(exc),
            }

