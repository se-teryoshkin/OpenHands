"""LangGraph-based Code Review ReAct Agent.

This module implements a code review agent using LangGraph's create_react_agent.
The agent uses a ReAct (Reasoning + Acting) pattern to:
1. Analyze specification documents
2. Review generated code against specifications
3. Identify issues and provide actionable feedback
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.graph.state import CompiledStateGraph

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import ReviewResult, ReviewComment
from openhands.agenthub.langgraph_reviewer_agent.tools import ALL_TOOLS
from openhands.agenthub.langgraph_reviewer_agent.tools.report_tool import (
    reset_review_state,
    get_review_comments,
    get_files_reviewed,
)


# Configure module logger
logger = logging.getLogger("code_review_agent")


def setup_debug_logging(level: int = logging.DEBUG):
    """Setup detailed debug logging for the agent.

    Args:
        level: Logging level (default DEBUG)
    """
    # Create formatter with detailed info
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Set up the logger
    logger.setLevel(level)
    logger.handlers = []  # Clear existing handlers
    logger.addHandler(console_handler)

    # Also configure langchain/langgraph logging if debug
    if level == logging.DEBUG:
        for log_name in ["langchain", "langgraph", "httpx"]:
            log = logging.getLogger(log_name)
            log.setLevel(logging.WARNING)  # Keep these quieter

    return logger


# System prompt for the code review agent
SYSTEM_PROMPT = """You are an expert code review agent. Your task is to review generated code against a specification and identify issues.

## Your Review Process

1. **Read the Specification**: First, understand what the code should implement by reading the spec file.

2. **Find Generated Code**: Locate and read all Python source files and test files.

3. **Analyze Code Structure**: Use extract_signatures_tool to understand the implemented classes and methods.

4. **Validate Against Specification**:
   - Use validate_signatures_tool to check if method signatures match the spec
   - Use validate_field_access_tool to check for access to non-existent fields
   - Use validate_mapping_tool to verify data mappings are complete
   - Use validate_model_field_mapping_tool for detailed field-level validation between source/target models
   - Use validate_test_quality_tool to assess test coverage
   - Use validate_code_quality_tool to detect anti-patterns (bare except, exception suppression, mocks in prod code)

5. **Check Pydantic and Models**:
   - Use validate_pydantic_usage_tool to check models inherit from BaseModel
   - Use validate_api_model_compliance_tool when API data structures are provided to verify model fields match
   - Flag duplicate model definitions across files

6. **Check Project Structure**:
   - Use validate_project_structure_tool to check file organization
   - Look for misplaced files (e.g., SOLUTION_SUMMARY.md in root)
   - Verify module directory structure

7. **Report Issues**: For each issue found, use create_review_comment_tool with:
   - Appropriate category and severity
   - Clear description of the issue
   - Suggestion for how to fix it

8. **Finalize**: When done, use finalize_review_tool to generate the complete report.

## Issue Categories

- `signature_mismatch`: Method signatures don't match specification
- `field_access_error`: Accessing non-existent fields on objects
- `mapping_incomplete`: Missing required field mappings
- `field_mapping_error`: Field-level mapping issues (missing required fields in data transformations)
- `test_quality`: Test coverage or quality issues
- `structure_issue`: File organization problems
- `missing_implementation`: Required features not implemented
- `type_error`: Type annotation issues
- `pydantic_issue`: Issues with Pydantic model usage
- `code_quality_issue`: Anti-patterns (bare except, exception suppression, mocks in production)
- `guideline_violation`: Code violates provided coding guidelines
- `scope_violation`: Module implements functionality outside its defined scope
- `data_structure_mismatch`: Implementation models don't match API data structure definitions
- `general`: Other issues

## Severity Levels

- `error`: Critical issues that must be fixed (incorrect implementation, missing features)
- `warning`: Issues that should be addressed (suboptimal patterns, potential bugs)
- `info`: Suggestions for improvement (style, documentation)

## Important Guidelines

- Be thorough but focused - check what matters most based on the spec
- Provide actionable feedback with specific file locations and line numbers
- Include suggestions for fixes when possible
- If mocking is explicitly forbidden in the spec, flag any use of mocks as errors
- Check that all required interface methods are implemented
- Verify data model field mappings are correct

## Using Additional Context Documents

When provided, use these additional documents for more thorough reviews:

### API Data Structures
The API data structures document is a SPECIFICATION describing what models SHOULD look like.
It is documentation, NOT importable code. Models that implement this spec are CORRECT.

If data structure definitions are provided:
- Use validate_pydantic_usage_tool to check for duplicate models across implementation files
- Compare implementation model fields with the spec definitions (types, optionality)
- Check that required fields are properly implemented
- Flag ONLY if same model is defined in MULTIPLE implementation files (real duplication)
- Do NOT suggest "importing from API data structures" - the spec is documentation, not code

### Coding Guidelines
If coding guidelines are provided:
- Check implementation follows the recommended patterns
- Flag violations of explicit "do not" rules (e.g., "no manual agent loops")
- Verify tools/modules are structured as recommended
- Check naming conventions match the guidelines

### Module Architecture
If module description is provided:
- Verify implementation stays within the module's stated scope
- Flag if the module implements functionality that belongs to another module
- Check that module dependencies are correct
- Verify the module doesn't duplicate functionality from other modules

## CRITICAL Rules for Efficiency

1. **ONLY use the tools listed above** - DO NOT invent tools like "search" or "grep"
2. **Use absolute file paths** - do NOT use relative paths like "../.."
3. **DO NOT repeat failed tool calls** - if a tool returns an error, try a different approach
4. **After reporting issues with create_review_comment_tool, FINISH by calling finalize_review_tool**
5. **Limit exploration** - read each file only once, use find_python_files_tool to discover files
6. **Be decisive** - identify issues quickly, report them, and finalize

## REQUIRED Checks (must do ALL of these - DO NOT SKIP ANY)

You MUST call each of these tools at least once per review:

1. **validate_signatures_tool** - Check ALL interface methods match the spec
2. **validate_test_quality_tool** - Analyze test files for superficial tests
3. **validate_code_quality_tool** - Detect anti-patterns (bare except, mock in prod code)
4. **validate_pydantic_usage_tool** - Check for duplicate/redefined models
5. **validate_project_structure_tool** - Check for misplaced files (SOLUTION_SUMMARY.md, etc.)

## CRITICAL: Reporting Issues

After EACH validator tool returns results:
- Look at the "issues" array in the result
- For EACH issue in the array, call create_review_comment_tool
- Do NOT skip any issues - report ALL of them
- Include issues from ALL categories: pydantic_issue, structure_issue, code_quality_issue, etc.

Example: If validate_project_structure_tool returns {"issues": [{"type": "misplaced_file", ...}]}
You MUST call create_review_comment_tool for that misplaced_file issue.

IMPORTANT: Run ALL validators, report ALL issues found, then call finalize_review_tool!

## Field-Level Mapping Validation

When the specification defines a target model that should be created from a source model, use validate_model_field_mapping_tool to:
1. Extract all fields from both source and target model definitions
2. Check that every required field in the target model is being populated
3. Identify fields that exist in source but are missing in target mapping
4. Verify type compatibility between source and target fields
5. Report any unmapped required fields as errors with suggestions for mapping

Common field mapping issues to detect:
- Missing required fields that have similar names in source (check for naming variations)
- Fields with slightly different names between source and target models
- Incorrect type conversions between source and target field types
- Optional fields in source being mapped to required fields in target

## Code Quality Validation

Use validate_code_quality_tool on each Python file to detect anti-patterns:

### Anti-patterns to detect:
1. **Bare except** (`except:`) - Catches everything including KeyboardInterrupt
2. **Broad exception handling** (`except Exception:`) without re-raise - Silently suppresses errors
3. **Exception suppression** (`except: pass` or `except Exception: pass`) - Hides errors
4. **Mocking in production code** - MagicMock, Mock, patch should only be in test files

### When to flag as error (code_quality_issue category):
- `try: ... except Exception: <no re-raise>` → ERROR: Must either handle specific exceptions or re-raise
- `try: ... except: pass` → ERROR: Never silently suppress exceptions
- `MagicMock()` in service.py → ERROR: Mocking only allowed in test files
- Empty except blocks → ERROR: At minimum, log the exception

### Example issues:
- Bare `try: ... except Exception:` without re-raising or logging
- Using Mock/MagicMock in production code instead of test files
"""


class AgentState(TypedDict):
    """State for the review agent."""
    messages: list


class CodeReviewAgent:
    """LangGraph-based Code Review Agent using ReAct pattern.

    Uses separate LLM configuration from environment variables:
    - GPT_OSS_HOST: Base URL for the LLM API
    - GPT_OSS_KEY: API key
    - GPT_OSS_MODEL_NAME: Model name
    """

    def __init__(self, config: ReviewAgentConfig | None = None, debug: bool = False):
        """Initialize the code review agent.

        Args:
            config: Optional configuration. If not provided, uses defaults from env.
            debug: If True, enable detailed debug logging.
        """
        self.config = config or ReviewAgentConfig.from_env()
        self._agent: CompiledStateGraph | None = None
        self._llm: ChatOpenAI | None = None
        self.debug = debug or self.config.verbose

        if self.debug:
            setup_debug_logging(logging.DEBUG)

        self._step_count = 0
        self._start_time: datetime | None = None

    def _log_step(self, step_type: str, content: str, **kwargs):
        """Log a step in the agent execution.

        Args:
            step_type: Type of step (THOUGHT, TOOL_CALL, TOOL_RESULT, etc.)
            content: Content to log
            **kwargs: Additional key-value pairs to log
        """
        if not self.debug:
            return

        self._step_count += 1
        elapsed = ""
        if self._start_time:
            elapsed = f" [+{(datetime.now() - self._start_time).total_seconds():.1f}s]"

        # Format the log message
        header = f"═══ Step {self._step_count}: {step_type}{elapsed} ═══"
        logger.debug("=" * len(header))
        logger.debug(header)
        logger.debug("=" * len(header))

        # Log content (truncate if too long)
        if len(content) > 2000:
            logger.debug(f"{content[:2000]}... (truncated, {len(content)} chars total)")
        else:
            logger.debug(content)

        # Log any additional kwargs
        for key, value in kwargs.items():
            if isinstance(value, (dict, list)):
                logger.debug(f"  {key}: {json.dumps(value, indent=2, ensure_ascii=False)[:500]}")
            else:
                logger.debug(f"  {key}: {value}")

    @property
    def llm(self) -> ChatOpenAI:
        """Get the LLM instance, creating it if needed."""
        if self._llm is None:
            logger.debug(f"Creating LLM: model={self.config.llm_model_name}, "
                        f"base_url={self.config.llm_base_url}")
            self._llm = ChatOpenAI(
                model=self.config.llm_model_name,
                temperature=self.config.temperature,
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    @property
    def agent(self) -> CompiledStateGraph:
        """Get the agent graph, creating it if needed."""
        if self._agent is None:
            # Select tools based on config
            tools = []
            for tool in ALL_TOOLS:
                tool_name = tool.name

                # Filter based on config
                if "signature" in tool_name and not self.config.enable_signature_validation:
                    continue
                if "field" in tool_name and not self.config.enable_field_validation:
                    continue
                if "test" in tool_name and not self.config.enable_test_validation:
                    continue
                if "structure" in tool_name and not self.config.enable_structure_validation:
                    continue

                tools.append(tool)

            tool_names = [t.name for t in tools]
            logger.debug(f"Creating ReAct agent with {len(tools)} tools: {tool_names}")

            # Create the ReAct agent using langgraph
            self._agent = create_react_agent(
                model=self.llm,
                tools=tools,
                prompt=SYSTEM_PROMPT,
            )

        return self._agent

    def review(
        self,
        spec_path: str,
        code_root: str,
        module_name: str,
        component_docs: str | None = None,
        data_structures: str | None = None,
        coding_guidelines: str | None = None,
        modules_description: str | None = None,
    ) -> ReviewResult:
        """Run a code review.

        Args:
            spec_path: Path to the specification file.
            code_root: Root directory of the generated code.
            module_name: Name of the module being reviewed.
            component_docs: Optional documentation of external components
                used in the code (for field validation).
            data_structures: Optional API data structures documentation
                (e.g., Pydantic models) for checking implementation matches.
            coding_guidelines: Optional coding guidelines/best practices
                that the implementation should follow.
            modules_description: Optional high-level module architecture
                description for checking implementation scope.

        Returns:
            ReviewResult containing all findings.
        """
        # Reset state for new review
        reset_review_state()
        self._step_count = 0
        self._start_time = datetime.now()

        logger.info(f"Starting code review for module: {module_name}")
        logger.info(f"  Spec: {spec_path}")
        logger.info(f"  Code: {code_root}")

        # Build the review request message
        request_parts = [
            f"Please review the generated code for module '{module_name}'.",
            "",
            f"**Specification file:** {spec_path}",
            f"**Code root directory:** {code_root}",
            "",
            "## MANDATORY STEPS (Execute ALL in order):",
            "",
            "### Phase 1: Discovery",
            "1. Read the specification file",
            "2. Use find_python_files_tool to discover all Python files",
            "3. Read source files (service.py, models.py, etc.)",
            "",
            "### Phase 2: Validation (CALL ALL VALIDATORS)",
            "4. CALL validate_signatures_tool - check method signatures",
            "5. CALL validate_test_quality_tool - check test quality",
            "6. CALL validate_code_quality_tool - check for anti-patterns",
            "7. CALL validate_pydantic_usage_tool - check for duplicate models",
            "8. CALL validate_project_structure_tool - check file organization",
            "",
            "### Phase 3: Reporting",
            "9. For EACH issue found by validators → create_review_comment_tool",
            "10. CALL finalize_review_tool to complete review",
            "",
            "⚠️ WARNING: Skipping ANY validator step makes the review incomplete!",
        ]

        if component_docs:
            request_parts.extend([
                "",
                "**Component Documentation:**",
                "Use this to validate field accesses on external objects:",
                component_docs,
            ])

        if data_structures:
            request_parts.extend([
                "",
                "**API Data Structures Specification:**",
                "This is a SPECIFICATION document describing how models SHOULD look.",
                "It is documentation, NOT importable code. Models implementing this spec are CORRECT.",
                "Use validate_pydantic_usage_tool to check for duplicate models across implementation files.",
                "Do NOT suggest importing from this spec - implementations are correct.",
                "Specification:",
                data_structures[:4000],  # Truncate if too long
            ])

        if coding_guidelines:
            request_parts.extend([
                "",
                "**Coding Guidelines:**",
                "Check that implementation follows these guidelines:",
                coding_guidelines[:3000],  # Truncate if too long
            ])

        if modules_description:
            request_parts.extend([
                "",
                "**Module Architecture Description:**",
                "Verify implementation scope matches the module's purpose.",
                "Check that module doesn't implement functionality outside its scope:",
                modules_description[:2000],  # Truncate if too long
            ])

        request = "\n".join(request_parts)

        self._log_step("USER_REQUEST", request)

        # Run the agent with streaming to capture all steps
        review_result = None

        # Set recursion limit based on max_iterations config
        stream_config = {"recursion_limit": self.config.max_iterations * 3}

        try:
            for event in self.agent.stream(
                {"messages": [HumanMessage(content=request)]},
                stream_mode="values",
                config=stream_config,
            ):
                messages = event.get("messages", [])
                if not messages:
                    continue

                last_msg = messages[-1]

                # Log based on message type
                if isinstance(last_msg, AIMessage):
                    # Check for tool calls
                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        for tc in last_msg.tool_calls:
                            self._log_step(
                                "TOOL_CALL",
                                f"Calling tool: {tc['name']}",
                                tool_name=tc["name"],
                                arguments=tc.get("args", {})
                            )
                    elif last_msg.content:
                        # This is a thought/reasoning step
                        content = last_msg.content
                        if isinstance(content, str):
                            if '"module_name"' in content and '"passed"' in content:
                                self._log_step("FINAL_RESULT", content)
                                try:
                                    review_result = json.loads(content)
                                except json.JSONDecodeError:
                                    pass
                            else:
                                self._log_step("THOUGHT", content)

                elif isinstance(last_msg, ToolMessage):
                    # Tool result
                    content = last_msg.content
                    tool_name = getattr(last_msg, "name", "unknown")
                    self._log_step(
                        "TOOL_RESULT",
                        f"Result from {tool_name}:",
                        result=content[:1000] if len(content) > 1000 else content
                    )

        except Exception as e:
            # Log the error but continue with partial results
            logger.warning(f"Agent stopped early: {e}")
            logger.info("Returning partial results collected so far...")

        # Always collect comments from the review state
        # The agent may output its own summary but comments are recorded via tools
        collected_comments = get_review_comments()
        collected_files = get_files_reviewed()

        # If no proper result, construct one from collected comments
        if not review_result:
            error_count = sum(1 for c in collected_comments if c.get("severity") == "error")
            review_result = {
                "module_name": module_name,
                "passed": error_count == 0,
                "summary": "Review completed",
                "comments": collected_comments,
                "files_reviewed": collected_files,
            }
        else:
            # Agent provided summary, but use collected comments and files
            # as the agent may not include them in its JSON output
            if not review_result.get("comments"):
                review_result["comments"] = collected_comments
            if not review_result.get("files_reviewed"):
                review_result["files_reviewed"] = collected_files

        # Convert to ReviewResult model
        comments = [
            ReviewComment(
                category=c.get("category", "general"),
                severity=c.get("severity", "info"),
                file_path=c.get("file_path", ""),
                message=c.get("message", ""),
                line_number=c.get("line_number"),
                suggestion=c.get("suggestion"),
                spec_reference=c.get("spec_reference"),
            )
            for c in review_result.get("comments", [])
        ]

        elapsed = (datetime.now() - self._start_time).total_seconds()
        logger.info(f"Review completed in {elapsed:.1f}s with {self._step_count} steps")
        logger.info(f"  Errors: {sum(1 for c in comments if c.severity.value == 'error')}")
        logger.info(f"  Warnings: {sum(1 for c in comments if c.severity.value == 'warning')}")

        return ReviewResult(
            module_name=review_result.get("module_name", module_name),
            passed=review_result.get("passed", len([c for c in comments if c.severity.value == "error"]) == 0),
            comments=comments,
            summary=review_result.get("summary", ""),
            files_reviewed=review_result.get("files_reviewed", []),
        )

    def review_with_streaming(
        self,
        spec_path: str,
        code_root: str,
        module_name: str,
        component_docs: str | None = None,
    ):
        """Run a code review with streaming output.

        Yields intermediate messages as the agent works.

        Args:
            spec_path: Path to the specification file.
            code_root: Root directory of the generated code.
            module_name: Name of the module being reviewed.
            component_docs: Optional documentation of external components.

        Yields:
            Tuples of (event_type, content) where event_type is 'thought',
            'tool_call', 'tool_result', or 'final'.
        """
        # Reset state
        reset_review_state()
        self._step_count = 0
        self._start_time = datetime.now()

        # Build request
        request_parts = [
            f"Please review the generated code for module '{module_name}'.",
            "",
            f"**Specification file:** {spec_path}",
            f"**Code root directory:** {code_root}",
        ]

        if component_docs:
            request_parts.extend([
                "",
                "**Component Documentation:**",
                component_docs,
            ])

        request = "\n".join(request_parts)

        # Stream the agent execution
        config = {"recursion_limit": self.config.max_iterations * 3}

        for event in self.agent.stream(
            {"messages": [HumanMessage(content=request)]},
            stream_mode="values",
            config=config,
        ):
            messages = event.get("messages", [])
            if not messages:
                continue

            last_msg = messages[-1]

            # Determine event type based on message
            if isinstance(last_msg, AIMessage):
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        self._log_step(
                            "TOOL_CALL",
                            f"Calling: {tc['name']}",
                            arguments=tc.get("args", {})
                        )
                        yield ("tool_call", {
                            "name": tc["name"],
                            "arguments": tc.get("args", {})
                        })
                elif last_msg.content:
                    content = last_msg.content
                    if isinstance(content, str):
                        if '"module_name"' in content and '"passed"' in content:
                            self._log_step("FINAL_RESULT", content)
                            yield ("final", content)
                        else:
                            self._log_step("THOUGHT", content)
                            yield ("thought", content)

            elif isinstance(last_msg, ToolMessage):
                content = last_msg.content
                tool_name = getattr(last_msg, "name", "unknown")
                self._log_step("TOOL_RESULT", f"Result from {tool_name}")
                yield ("tool_result", {
                    "tool": tool_name,
                    "result": content
                })


def create_review_agent(
    config: ReviewAgentConfig | None = None,
    debug: bool = False
) -> CodeReviewAgent:
    """Factory function to create a code review agent.

    Args:
        config: Optional configuration for the agent.
        debug: If True, enable detailed debug logging.

    Returns:
        Configured CodeReviewAgent instance.
    """
    return CodeReviewAgent(config, debug=debug)


def run_review(
    spec_path: str,
    code_root: str,
    module_name: str,
    config: ReviewAgentConfig | None = None,
    component_docs: str | None = None,
    debug: bool = False,
) -> ReviewResult:
    """Convenience function to run a code review.

    Args:
        spec_path: Path to the specification file.
        code_root: Root directory of the generated code.
        module_name: Name of the module being reviewed.
        config: Optional configuration for the agent.
        component_docs: Optional documentation of external components.
        debug: If True, enable detailed debug logging.

    Returns:
        ReviewResult containing all findings.
    """
    agent = create_review_agent(config, debug=debug)
    return agent.review(spec_path, code_root, module_name, component_docs)
