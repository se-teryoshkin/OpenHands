"""ReAct agent dedicated to design pattern identification (pattern scout).

Reads the full codebase via tools (find_python_files_tool, read_file_tool) and
reports identified patterns via report_patterns_tool with Pydantic-validated output.
"""

import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import SecretStr

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import PatternIdentificationOutput
from openhands.agenthub.langgraph_reviewer_agent.tools.file_tools import (
    find_python_files_tool,
    read_file_tool,
    list_files_tool,
)
from openhands.agenthub.langgraph_reviewer_agent.tools.pattern_scout_tools import (
    make_report_patterns_tool,
)

logger = logging.getLogger("code_review_agent")

PATTERN_SCOUT_SYSTEM_PROMPT = """You are a design pattern scout. Your ONLY task is to identify every design pattern used in the given codebase.

## Process
1. Use find_python_files_tool to discover all Python files under the code root.
2. Read source files (and tests if relevant) with read_file_tool. Read as many files as needed to get full context — do not skip files. You may use list_files_tool to explore directories.
3. For each design pattern you find (e.g. Singleton, Factory, Adapter, Protocol/Interface, Builder, Dependency Injection), note:
   - pattern_name: canonical name of the pattern
   - file_path: full path to the file (as returned by read_file_tool)
   - line_numbers: list of line numbers where the pattern is evident
   - class_or_function_names: names of classes or functions that implement or use this pattern
   - rationale: brief explanation of why this pattern is used here
4. When you have finished reading the codebase and listing all patterns, call report_patterns_tool ONCE with the complete list. If no clear design patterns are found, call report_patterns_tool with an empty list.

## Rules
- Read files in full when needed; use multiple read_file_tool calls to cover the whole codebase.
- Include only clearly identifiable named patterns (Singleton, Factory, Adapter, etc.), not generic OOP.
- You MUST call report_patterns_tool exactly once when done. The argument must be a single object with key "patterns" and value a list of pattern objects. Each pattern object must have: pattern_name (string), file_path (string), line_numbers (list of integers), class_or_function_names (list of strings), rationale (string).
- Do not invent patterns; only report what is clearly present in the code.
"""


class PatternScoutAgent:
    """ReAct agent that identifies design patterns by reading the codebase via tools."""

    def __init__(
        self,
        config: ReviewAgentConfig | None = None,
        max_steps: int = 50,
        verbose: bool = False,
        callbacks: list[Any] | None = None,
    ):
        self.config = config or ReviewAgentConfig.from_env()
        self.max_steps = max_steps
        self.verbose = verbose
        self.callbacks = callbacks
        self._llm: ChatOpenAI | None = None
        self._agent = None
        self._result_holder: dict = {}
        self._report_tool = None

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            api_key = SecretStr(self.config.llm_api_key) if self.config.llm_api_key else None
            self._llm = ChatOpenAI(
                model=self.config.llm_model_name,
                temperature=self.config.temperature,
                api_key=api_key,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    @property
    def agent(self):
        if self._agent is None:
            self._result_holder = {}
            self._report_tool = make_report_patterns_tool(self._result_holder)
            tools = [
                find_python_files_tool,
                read_file_tool,
                list_files_tool,
                self._report_tool,
            ]
            self._agent = create_react_agent(
                model=self.llm,
                tools=tools,
                prompt=PATTERN_SCOUT_SYSTEM_PROMPT,
            )
        return self._agent

    def run(
        self,
        spec_path: str,
        code_root: str,
    ) -> PatternIdentificationOutput:
        """Run the pattern scout: discover files, read code, identify patterns, return validated output.

        Args:
            spec_path: Path to the specification file (for context in the prompt).
            code_root: Root directory of the code to analyze.

        Returns:
            PatternIdentificationOutput with all identified patterns (Pydantic-validated).
            If the agent never calls report_patterns_tool or validation fails, returns empty list.
        """
        code_root_resolved = str(Path(code_root).resolve())
        spec_path_resolved = str(Path(spec_path).resolve())

        user_content = f"""Identify all design patterns in this codebase.

- Specification file (for context): {spec_path_resolved}
- Code root directory: {code_root_resolved}

Use find_python_files_tool with root_path="{code_root_resolved}", then read the Python files with read_file_tool. When you have identified every design pattern, call report_patterns_tool with the complete list (or with an empty list if none found)."""

        # Ensure we have a fresh result_holder for this run
        self._result_holder = {}
        self._report_tool = make_report_patterns_tool(self._result_holder)
        tools = [
            find_python_files_tool,
            read_file_tool,
            list_files_tool,
            self._report_tool,
        ]
        self._agent = create_react_agent(
            model=self.llm,
            tools=tools,
            prompt=PATTERN_SCOUT_SYSTEM_PROMPT,
        )

        logger.info("Pattern scout: starting (ReAct agent)")
        run_config: dict[str, Any] = {"recursion_limit": self.max_steps * 3}
        if self.callbacks:
            run_config["callbacks"] = self.callbacks
            run_config["metadata"] = {"review_phase": "pattern_scout"}
            run_config["tags"] = ["reviewer-agent", "pattern-scout"]
        try:
            if self.verbose:
                for event in self.agent.stream(
                    {"messages": [HumanMessage(content=user_content)]},
                    config=run_config,
                    stream_mode="values",
                ):
                    messages = event.get("messages", [])
                    if not messages:
                        continue
                    last = messages[-1]
                    if isinstance(last, AIMessage):
                        if last.content:
                            logger.info("[pattern scout LLM] %s", last.content[:2000])
                        if getattr(last, "tool_calls", None):
                            for tc in last.tool_calls:
                                name = tc.get("name", "?")
                                args = tc.get("args", {})
                                args_str = str(args)[:500]
                                logger.info("[pattern scout LLM] tool_call: %s %s", name, args_str)
                    elif isinstance(last, ToolMessage):
                        name = getattr(last, "name", "?")
                        content = (last.content or "")[:1500]
                        logger.info("[pattern scout tool] %s -> %s", name, content)
            else:
                self.agent.invoke(
                    {"messages": [HumanMessage(content=user_content)]},
                    config=run_config,
                )
        except Exception as e:
            logger.warning(f"Pattern scout agent stopped: {e}")

        out = self._result_holder.get("pattern_output")
        if out is None:
            logger.info("Pattern scout: no report_patterns_tool call or validation failed; returning empty list")
            return PatternIdentificationOutput(patterns=[])
        assert isinstance(out, PatternIdentificationOutput)
        logger.info(f"Pattern scout: reported {len(out.patterns)} pattern(s)")
        return out
