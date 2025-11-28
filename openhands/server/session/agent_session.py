import asyncio
import json
import re
import shlex
import time
from datetime import datetime, timezone
from logging import LoggerAdapter
from pathlib import Path
from types import MappingProxyType
from typing import Callable, cast

from openhands.controller import AgentController
from openhands.controller.agent import Agent
from openhands.controller.replay import ReplayManager
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig, LLMConfig, OpenHandsConfig
from openhands.core.exceptions import AgentRuntimeUnavailableError
from openhands.core.logger import OpenHandsLoggerAdapter
from openhands.core.schema.agent import AgentState
from openhands.events.action import (
    ChangeAgentStateAction,
    CmdRunAction,
    FileReadAction,
    FileWriteAction,
    MessageAction,
)
from openhands.events.event import Event, EventSource
from openhands.events.observation.error import ErrorObservation
from openhands.events.observation.files import FileReadObservation
from openhands.events.stream import EventStream, EventStreamSubscriber
from openhands.integrations.provider import (
    CUSTOM_SECRETS_TYPE,
    PROVIDER_TOKEN_TYPE,
    ProviderHandler,
)
from openhands.llm.llm_registry import LLMRegistry
from openhands.mcp import add_mcp_tools_to_agent
from openhands.memory.memory import Memory
from openhands.microagent.microagent import BaseMicroagent
from openhands.runtime import get_runtime_cls
from openhands.runtime.base import Runtime
from openhands.runtime.impl.remote.remote_runtime import RemoteRuntime
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.server.monitoring import MonitoringListener
from openhands.server.services.conversation_stats import ConversationStats
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.files import FileStore
from openhands.utils.async_utils import EXECUTOR, call_sync_from_async
from openhands.utils.shutdown_listener import should_continue

WAIT_TIME_BEFORE_CLOSE = 90
WAIT_TIME_BEFORE_CLOSE_INTERVAL = 5

MD_REFERENCE_PATTERN = re.compile(r'(?:\./|/)?[\w.\-/]+\.md', re.IGNORECASE)
_MONITORING_CALLBACK_ID = 'monitoring_listener'


class AgentSession:
    """Represents a session with an Agent

    Attributes:
        controller: The AgentController instance for controlling the agent.
    """

    sid: str
    user_id: str | None
    event_stream: EventStream
    llm_registry: LLMRegistry
    file_store: FileStore
    controller: AgentController | None = None
    runtime: Runtime | None = None

    memory: Memory | None = None
    _starting: bool = False
    _started_at: float = 0
    _closed: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    logger: LoggerAdapter

    def __init__(
        self,
        sid: str,
        file_store: FileStore,
        llm_registry: LLMRegistry,
        conversation_stats: ConversationStats,
        status_callback: Callable | None = None,
        user_id: str | None = None,
        monitoring_listener: MonitoringListener | None = None,
    ) -> None:
        """Initializes a new instance of the Session class

        Parameters:
        - sid: The session ID
        - file_store: Instance of the FileStore
        """
        self.sid = sid
        self.event_stream = EventStream(sid, file_store, user_id)
        self.file_store = file_store
        self._status_callback = status_callback
        self.user_id = user_id
        self.logger = OpenHandsLoggerAdapter(
            extra={'session_id': sid, 'user_id': user_id}
        )
        self.llm_registry = llm_registry
        self.conversation_stats = conversation_stats
        self._spec_written: bool = False
        self._repo_directory: str | None = None
        self._selected_repository: str | None = None
        self._selected_branch: str | None = None
        self._conversation_instructions: str | None = None
        self.monitoring_listener = monitoring_listener
        self._monitoring_callback_registered = False
        self.analytics_enabled = False

        if self.monitoring_listener is not None:
            try:
                self.event_stream.subscribe(
                    EventStreamSubscriber.MAIN,
                    self._handle_monitoring_event,
                    _MONITORING_CALLBACK_ID,
                )
                self._monitoring_callback_registered = True
            except Exception as exc:
                self.logger.debug(
                    'Failed to subscribe monitoring listener: %s', exc
                )

    def set_user_analytics_consent(self, consent: bool | None) -> None:
        """Enable or disable analytics forwarding based on user consent."""
        self.analytics_enabled = bool(consent) and self.monitoring_listener is not None

    def _handle_monitoring_event(self, event: Event) -> None:
        if not self.analytics_enabled or self.monitoring_listener is None:
            return
        try:
            self.monitoring_listener.on_session_event(self.sid, event)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug('Failed to forward monitoring event: %s', exc)

    async def start(
        self,
        runtime_name: str,
        config: OpenHandsConfig,
        agent: Agent,
        max_iterations: int,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
        custom_secrets: CUSTOM_SECRETS_TYPE | None = None,
        max_budget_per_task: float | None = None,
        agent_to_llm_config: dict[str, LLMConfig] | None = None,
        agent_configs: dict[str, AgentConfig] | None = None,
        selected_repository: str | None = None,
        selected_branch: str | None = None,
        initial_message: MessageAction | None = None,
        conversation_instructions: str | None = None,
        replay_json: str | None = None,
    ) -> None:
        """Starts the Agent session
        Parameters:
        - runtime_name: The name of the runtime associated with the session
        - config:
        - agent:
        - max_iterations:
        - max_budget_per_task:
        - agent_to_llm_config:
        - agent_configs:
        """
        if self.controller or self.runtime:
            raise RuntimeError(
                'Session already started. You need to close this session and start a new one.'
            )

        if self._closed:
            self.logger.warning('Session closed before starting')
            return
        self._starting = True
        started_at = time.time()
        self._started_at = started_at
        finished = False  # For monitoring
        runtime_connected = False
        restored_state = False
        custom_secrets_handler = Secrets(
            custom_secrets=custom_secrets if custom_secrets else {}  # type: ignore[arg-type]
        )
        try:
            runtime_connected = await self._create_runtime(
                runtime_name=runtime_name,
                config=config,
                agent=agent,
                git_provider_tokens=git_provider_tokens,
                custom_secrets=custom_secrets,
                selected_repository=selected_repository,
                selected_branch=selected_branch,
            )

            repo_directory = None
            if self.runtime and runtime_connected and selected_repository:
                repo_directory = selected_repository.split('/')[-1]

            self._repo_directory = repo_directory
            self._selected_repository = selected_repository
            self._selected_branch = selected_branch
            self._conversation_instructions = conversation_instructions

            if runtime_connected:
                self._spec_written = await self._maybe_write_spec_file(
                    initial_message=initial_message,
                    conversation_instructions=conversation_instructions,
                    repo_directory=repo_directory,
                    selected_repository=selected_repository,
                    selected_branch=selected_branch,
                )

            if git_provider_tokens:
                provider_handler = ProviderHandler(provider_tokens=git_provider_tokens)
                await provider_handler.set_event_stream_secrets(self.event_stream)

            if custom_secrets:
                custom_secrets_handler.set_event_stream_secrets(self.event_stream)

            self.memory = await self._create_memory(
                selected_repository=selected_repository,
                repo_directory=repo_directory,
                selected_branch=selected_branch,
                conversation_instructions=conversation_instructions,
                custom_secrets_descriptions=custom_secrets_handler.get_custom_secrets_descriptions(),
                working_dir=config.workspace_mount_path_in_sandbox,
            )

            # NOTE: this needs to happen before controller is created
            # so MCP tools can be included into the SystemMessageAction
            if self.runtime and runtime_connected and agent.config.enable_mcp:
                await add_mcp_tools_to_agent(agent, self.runtime, self.memory)

            if replay_json:
                initial_message = self._run_replay(
                    initial_message,
                    replay_json,
                    agent,
                    config,
                    max_iterations,
                    max_budget_per_task,
                    agent_to_llm_config,
                    agent_configs,
                )
            else:
                self.controller, restored_state = self._create_controller(
                    agent,
                    config.security.confirmation_mode,
                    max_iterations,
                    max_budget_per_task=max_budget_per_task,
                    agent_to_llm_config=agent_to_llm_config,
                    agent_configs=agent_configs,
                )

            if not self._closed:
                if initial_message:
                    self.event_stream.add_event(initial_message, EventSource.USER)
                    self.event_stream.add_event(
                        ChangeAgentStateAction(AgentState.RUNNING),
                        EventSource.ENVIRONMENT,
                    )
                else:
                    self.event_stream.add_event(
                        ChangeAgentStateAction(AgentState.AWAITING_USER_INPUT),
                        EventSource.ENVIRONMENT,
                    )
            finished = True
        finally:
            self._starting = False
            success = finished and runtime_connected
            duration = time.time() - started_at

            if self.analytics_enabled and self.monitoring_listener is not None:
                try:
                    self.monitoring_listener.on_agent_session_start(
                        success,
                        duration,
                        conversation_id=self.sid,
                        user_id=self.user_id,
                    )
                except Exception as exc:  # pragma: no cover - defensive logging
                    self.logger.debug(
                        'Failed to notify monitoring listener of session start: %s',
                        exc,
                    )

            log_metadata = {
                'signal': 'agent_session_start',
                'success': success,
                'duration': duration,
                'restored_state': restored_state,
            }
            if success:
                self.logger.info(
                    f'Agent session start succeeded in {duration}s', extra=log_metadata
                )
            else:
                self.logger.error(
                    f'Agent session start failed in {duration}s', extra=log_metadata
                )

    async def close(self) -> None:
        """Closes the Agent session"""
        if self._closed:
            return
        self._closed = True
        while self._starting and should_continue():
            self.logger.debug(
                f'Waiting for initialization to finish before closing session {self.sid}'
            )
            await asyncio.sleep(WAIT_TIME_BEFORE_CLOSE_INTERVAL)
            if time.time() <= self._started_at + WAIT_TIME_BEFORE_CLOSE:
                self.logger.error(
                    f'Waited too long for initialization to finish before closing session {self.sid}'
                )
                break
        if self._monitoring_callback_registered:
            try:
                self.event_stream.unsubscribe(
                    EventStreamSubscriber.MAIN, _MONITORING_CALLBACK_ID
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.debug(
                    'Failed to unsubscribe monitoring listener: %s', exc
                )
            self._monitoring_callback_registered = False

        if self.event_stream is not None:
            self.event_stream.close()
        if self.controller is not None:
            self.controller.save_state()
            await self.controller.close()
        if self.runtime is not None:
            EXECUTOR.submit(self.runtime.close)

    def _run_replay(
        self,
        initial_message: MessageAction | None,
        replay_json: str,
        agent: Agent,
        config: OpenHandsConfig,
        max_iterations: int,
        max_budget_per_task: float | None,
        agent_to_llm_config: dict[str, LLMConfig] | None,
        agent_configs: dict[str, AgentConfig] | None,
    ) -> MessageAction:
        """Replays a trajectory from a JSON file. Note that once the replay session
        finishes, the controller will continue to run with further user instructions,
        so we still need to pass llm configs, budget, etc., even though the replay
        itself does not call LLM or cost money.
        """
        assert initial_message is None
        replay_events = ReplayManager.get_replay_events(json.loads(replay_json))
        self.controller, _ = self._create_controller(
            agent,
            config.security.confirmation_mode,
            max_iterations,
            max_budget_per_task=max_budget_per_task,
            agent_to_llm_config=agent_to_llm_config,
            agent_configs=agent_configs,
            replay_events=replay_events[1:],
        )
        assert isinstance(replay_events[0], MessageAction)
        return replay_events[0]

    def override_provider_tokens_with_custom_secret(
        self,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None,
        custom_secrets: CUSTOM_SECRETS_TYPE | None,
    ):
        if git_provider_tokens and custom_secrets:
            # Use dictionary comprehension to avoid modifying dictionary during iteration
            tokens = {
                provider: token
                for provider, token in git_provider_tokens.items()
                if not (
                    ProviderHandler.get_provider_env_key(provider) in custom_secrets
                    or ProviderHandler.get_provider_env_key(provider).upper()
                    in custom_secrets
                )
            }
            return MappingProxyType(tokens)
        return git_provider_tokens

    async def _create_runtime(
        self,
        runtime_name: str,
        config: OpenHandsConfig,
        agent: Agent,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
        custom_secrets: CUSTOM_SECRETS_TYPE | None = None,
        selected_repository: str | None = None,
        selected_branch: str | None = None,
    ) -> bool:
        """Creates a runtime instance

        Parameters:
        - runtime_name: The name of the runtime associated with the session
        - config:
        - agent:

        Return True on successfully connected, False if could not connect.
        Raises if already created, possibly in other situations.
        """
        if self.runtime is not None:
            raise RuntimeError('Runtime already created')

        custom_secrets_handler = Secrets(custom_secrets=custom_secrets or {})  # type: ignore[arg-type]
        env_vars = custom_secrets_handler.get_env_vars()

        self.logger.debug(f'Initializing runtime `{runtime_name}` now...')
        runtime_cls = get_runtime_cls(runtime_name)
        if runtime_cls == RemoteRuntime:
            # If provider tokens is passed in custom secrets, then remove provider from provider tokens
            # We prioritize provider tokens set in custom secrets
            overrided_tokens = self.override_provider_tokens_with_custom_secret(
                git_provider_tokens, custom_secrets
            )

            self.runtime = runtime_cls(
                config=config,
                event_stream=self.event_stream,
                llm_registry=self.llm_registry,
                sid=self.sid,
                plugins=agent.sandbox_plugins,
                status_callback=self._status_callback,
                headless_mode=False,
                attach_to_existing=False,
                git_provider_tokens=overrided_tokens,
                env_vars=env_vars,
                user_id=self.user_id,
            )
        else:
            provider_handler = ProviderHandler(
                provider_tokens=git_provider_tokens
                or cast(PROVIDER_TOKEN_TYPE, MappingProxyType({}))
            )

            # Merge git provider tokens with custom secrets before passing over to runtime
            env_vars.update(await provider_handler.get_env_vars(expose_secrets=True))
            self.runtime = runtime_cls(
                config=config,
                event_stream=self.event_stream,
                llm_registry=self.llm_registry,
                sid=self.sid,
                plugins=agent.sandbox_plugins,
                status_callback=self._status_callback,
                headless_mode=False,
                attach_to_existing=False,
                env_vars=env_vars,
                git_provider_tokens=git_provider_tokens,
            )

        try:
            await self.runtime.connect()
        except AgentRuntimeUnavailableError as e:
            self.logger.error(f'Runtime initialization failed: {e}')
            if self._status_callback:
                self._status_callback(
                    'error', RuntimeStatus.ERROR_RUNTIME_DISCONNECTED, str(e)
                )
            return False

        await self.runtime.clone_or_init_repo(
            git_provider_tokens, selected_repository, selected_branch
        )
        await call_sync_from_async(self.runtime.maybe_run_setup_script)
        await call_sync_from_async(self.runtime.maybe_setup_git_hooks)

        self.logger.debug(
            f'Runtime initialized with plugins: {[plugin.name for plugin in self.runtime.plugins]}'
        )
        return True

    def _create_controller(
        self,
        agent: Agent,
        confirmation_mode: bool,
        max_iterations: int,
        max_budget_per_task: float | None = None,
        agent_to_llm_config: dict[str, LLMConfig] | None = None,
        agent_configs: dict[str, AgentConfig] | None = None,
        replay_events: list[Event] | None = None,
    ) -> tuple[AgentController, bool]:
        """Creates an AgentController instance

        Parameters:
        - agent:
        - confirmation_mode: Whether to use confirmation mode
        - max_iterations:
        - max_budget_per_task:
        - agent_to_llm_config:
        - agent_configs:

        Returns:
            Agent Controller and a bool indicating if state was restored from a previous conversation
        """
        if self.controller is not None:
            raise RuntimeError('Controller already created')
        if self.runtime is None:
            raise RuntimeError(
                'Runtime must be initialized before the agent controller'
            )

        msg = (
            '\n--------------------------------- OpenHands Configuration ---------------------------------\n'
            f'LLM: {agent.llm.config.model}\n'
            f'Base URL: {agent.llm.config.base_url}\n'
            f'Agent: {agent.name}\n'
            f'Runtime: {self.runtime.__class__.__name__}\n'
            f'Plugins: {[p.name for p in agent.sandbox_plugins] if agent.sandbox_plugins else "None"}\n'
            '-------------------------------------------------------------------------------------------'
        )
        self.logger.debug(msg)
        initial_state = self._maybe_restore_state()
        controller = AgentController(
            sid=self.sid,
            user_id=self.user_id,
            file_store=self.file_store,
            event_stream=self.event_stream,
            conversation_stats=self.conversation_stats,
            agent=agent,
            iteration_delta=int(max_iterations),
            budget_per_task_delta=max_budget_per_task,
            agent_to_llm_config=agent_to_llm_config,
            agent_configs=agent_configs,
            confirmation_mode=confirmation_mode,
            headless_mode=False,
            status_callback=self._status_callback,
            initial_state=initial_state,
            replay_events=replay_events,
            security_analyzer=self.runtime.security_analyzer if self.runtime else None,
        )

        return (controller, initial_state is not None)

    async def _create_memory(
        self,
        selected_repository: str | None,
        repo_directory: str | None,
        selected_branch: str | None,
        conversation_instructions: str | None,
        custom_secrets_descriptions: dict[str, str],
        working_dir: str,
    ) -> Memory:
        memory = Memory(
            event_stream=self.event_stream,
            sid=self.sid,
            status_callback=self._status_callback,
        )

        if self.runtime:
            # sets available hosts and other runtime info
            memory.set_runtime_info(
                self.runtime, custom_secrets_descriptions, working_dir
            )
            memory.set_conversation_instructions(conversation_instructions)

            # loads microagents from repo/.openhands/microagents
            microagents: list[BaseMicroagent] = await call_sync_from_async(
                self.runtime.get_microagents_from_selected_repo,
                selected_repository or None,
            )
            memory.load_user_workspace_microagents(microagents)

            if selected_repository and repo_directory:
                memory.set_repository_info(
                    selected_repository, repo_directory, selected_branch
                )
        return memory

    async def ensure_spec_file_for_message(self, message: MessageAction) -> None:
        """Ensure the specification file exists once a user message arrives."""
        if self._spec_written:
            return
        if not self.runtime:
            return
        spec_written = await self._maybe_write_spec_file(
            initial_message=message,
            conversation_instructions=self._conversation_instructions,
            repo_directory=self._repo_directory,
            selected_repository=self._selected_repository,
            selected_branch=self._selected_branch,
        )
        if spec_written:
            self._spec_written = True

    async def _maybe_write_spec_file(
        self,
        initial_message: MessageAction | None,
        conversation_instructions: str | None,
        repo_directory: str | None,
        selected_repository: str | None,
        selected_branch: str | None,
    ) -> bool:
        """Persist the initial specification to spec.md inside the workspace."""
        if not self.runtime or not initial_message:
            return False

        has_instruction_payload = any(
            [
                bool(initial_message.content and initial_message.content.strip()),
                bool(initial_message.file_urls),
                bool(initial_message.image_urls),
            ]
        )
        if not has_instruction_payload:
            return False

        spec_relative_path = (
            Path(repo_directory) / 'spec.md'
            if repo_directory
            else Path('spec.md')
        )
        spec_content = self._build_spec_markdown(
            initial_message=initial_message,
            conversation_instructions=conversation_instructions,
            selected_repository=selected_repository,
            selected_branch=selected_branch,
        )

        try:
            observation = await call_sync_from_async(
                self.runtime.write,
                FileWriteAction(
                    path=spec_relative_path.as_posix(),
                    content=spec_content,
                ),
            )
            if isinstance(observation, ErrorObservation):
                self.logger.warning(
                    'Failed to write spec.md to workspace: %s', observation.content
                )
                return False
        except Exception as exc:
            self.logger.warning(
                'Unexpected error while writing spec.md to workspace: %s', exc
            )
            return False

        await self._maybe_copy_referenced_files(
            initial_message=initial_message,
            conversation_instructions=conversation_instructions,
            destination_base=Path(repo_directory) if repo_directory else Path('.'),
        )
        return True

    def _build_spec_markdown(
        self,
        initial_message: MessageAction,
        conversation_instructions: str | None,
        selected_repository: str | None,
        selected_branch: str | None,
    ) -> str:
        """Format the task specification into Markdown."""
        timestamp = datetime.now(timezone.utc).isoformat()

        def _meta_line(label: str, value: str | None) -> str:
            return (
                f'- {label}: `{value}`'
                if value
                else f'- {label}: _not provided_'
            )

        lines: list[str] = [
            '# Task Specification',
            '',
            f'- Conversation ID: `{self.sid}`',
            f'- Created At: {timestamp}',
            _meta_line('Selected Repository', selected_repository),
            _meta_line('Selected Branch', selected_branch),
            '',
            '## User Instruction',
            '',
        ]

        content = initial_message.content or ''
        if content.strip():
            lines.append('```markdown')
            lines.append(content)
            lines.append('```')
        else:
            lines.append('_No user instruction provided._')
        lines.append('')

        if conversation_instructions and conversation_instructions.strip():
            lines.append('## Conversation Instructions')
            lines.append('')
            lines.append('```markdown')
            lines.append(conversation_instructions)
            lines.append('```')
            lines.append('')

        if initial_message.file_urls:
            lines.append('## Attached Files')
            lines.append('')
            for url in initial_message.file_urls:
                lines.append(f'- {url}')
            lines.append('')

        if initial_message.image_urls:
            lines.append('## Attached Images')
            lines.append('')
            for url in initial_message.image_urls:
                lines.append(f'- {url}')
            lines.append('')

        return '\n'.join(lines).rstrip() + '\n'

    async def _maybe_copy_referenced_files(
        self,
        initial_message: MessageAction,
        conversation_instructions: str | None,
        destination_base: Path,
    ) -> None:
        """Copy referenced markdown files into the destination directory."""
        if not self.runtime:
            return

        references = self._extract_markdown_references(
            initial_message.content, conversation_instructions
        )
        if not references:
            return

        seen: set[tuple[str, str]] = set()
        for reference in references:
            resolved = self._resolve_reference_path(reference)
            if not resolved:
                continue
            source_path, target_relative = resolved
            key = (source_path.as_posix(), target_relative.as_posix())
            if key in seen:
                continue
            seen.add(key)
            await self._copy_reference_into_repo(
                source_path=source_path,
                destination_base=destination_base,
                target_relative=target_relative,
            )

    def _extract_markdown_references(
        self, *texts: str | None
    ) -> list[str]:
        references: set[str] = set()
        for text in texts:
            if not text:
                continue
            for match in MD_REFERENCE_PATTERN.findall(text):
                cleaned = match.strip('`"\'()[]{}<>.,')
                if cleaned:
                    references.add(cleaned)
        return list(references)

    def _resolve_reference_path(
        self, reference: str
    ) -> tuple[Path, Path] | None:
        """Return absolute source path and relative target path within repo."""
        if not self.runtime or not reference or '://' in reference:
            return None

        workspace_root = self.runtime.workspace_root.resolve()
        appfactory_root = Path('/AppFactory-components')
        roots: list[tuple[Path, Path | None]] = [
            (workspace_root, None),
            (appfactory_root, Path('AppFactory-components')),
        ]

        normalized = reference.strip().replace('\\', '/')
        if not normalized:
            return None
        candidate_path = Path(normalized)
        candidate_resolved = candidate_path.resolve(strict=False)
        if candidate_resolved.is_absolute():
            abs_path = candidate_resolved
            for base, prefix in roots:
                base_resolved = base.resolve(strict=False)
                try:
                    rel = abs_path.relative_to(base_resolved)
                except ValueError:
                    continue
                target_rel = rel if prefix is None else prefix / rel
                return abs_path, target_rel
            return None

        # Relative path: treat as workspace-relative
        relative_str = normalized.lstrip('./')
        if not relative_str:
            return None
        relative_path = Path(relative_str)
        if '..' in relative_path.parts:
            return None
        abs_path = (workspace_root / relative_path).resolve(strict=False)
        try:
            rel = abs_path.relative_to(workspace_root)
        except ValueError:
            return None
        return abs_path, rel

    async def _copy_reference_into_repo(
        self, source_path: Path, destination_base: Path, target_relative: Path
    ) -> None:
        if not self.runtime:
            return

        read_obs = await call_sync_from_async(
            self.runtime.read, FileReadAction(path=source_path.as_posix())
        )
        if not isinstance(read_obs, FileReadObservation):
            self.logger.warning(
                'Unable to read referenced file %s: %s',
                source_path,
                getattr(read_obs, 'content', ''),
            )
            return

        target_path = destination_base / target_relative
        parent_dir = target_path.parent.as_posix()
        if parent_dir and parent_dir != '.':
            await call_sync_from_async(
                self.runtime.run,
                CmdRunAction(f'mkdir -p {shlex.quote(parent_dir)}'),
            )

        write_obs = await call_sync_from_async(
            self.runtime.write,
            FileWriteAction(path=target_path.as_posix(), content=read_obs.content),
        )
        if isinstance(write_obs, ErrorObservation):
            self.logger.warning(
                'Failed to copy referenced file %s into repository: %s',
                source_path,
                write_obs.content,
            )

    def get_state(self) -> AgentState | None:
        controller = self.controller
        if controller:
            return controller.state.agent_state
        if time.time() > self._started_at + WAIT_TIME_BEFORE_CLOSE:
            # If 5 minutes have elapsed and we still don't have a controller, something has gone wrong
            return AgentState.ERROR
        return None

    def _maybe_restore_state(self) -> State | None:
        """Helper method to handle state restore logic."""
        restored_state = None

        # Attempt to restore the state from session.
        # Use a heuristic to figure out if we should have a state:
        # if we have events in the stream.
        try:
            restored_state = State.restore_from_session(
                self.sid, self.file_store, self.user_id
            )
            self.logger.debug(f'Restored state from session, sid: {self.sid}')
        except Exception as e:
            if self.event_stream.get_latest_event_id() > 0:
                # if we have events, we should have a state
                self.logger.warning(f'State could not be restored: {e}')
            else:
                self.logger.debug('No events found, no state to restore')
        return restored_state

    def is_closed(self) -> bool:
        return self._closed
