"""LangGraph-based Code Review ReAct Agent.

This module implements a code review agent using LangGraph's create_react_agent.
The agent uses a ReAct (Reasoning + Acting) pattern to:
1. Analyze specification documents
2. Review generated code against specifications
3. Identify issues and provide actionable feedback
"""

import json
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.graph.graph import CompiledGraph

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import ReviewResult, ReviewComment
from openhands.agenthub.langgraph_reviewer_agent.tools import ALL_TOOLS
from openhands.agenthub.langgraph_reviewer_agent.tools.report_tool import (
    reset_review_state,
    get_review_comments,
    get_files_reviewed,
)


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
   - Use validate_test_quality_tool to assess test coverage

5. **Check Project Structure**: Use validate_structure_tool to ensure proper organization.

6. **Report Issues**: For each issue found, use create_review_comment_tool with:
   - Appropriate category and severity
   - Clear description of the issue
   - Suggestion for how to fix it

7. **Finalize**: When done, use finalize_review_tool to generate the complete report.

## Issue Categories

- `signature_mismatch`: Method signatures don't match specification
- `field_access_error`: Accessing non-existent fields on objects
- `mapping_incomplete`: Missing required field mappings
- `test_quality`: Test coverage or quality issues
- `structure_issue`: File organization problems
- `missing_implementation`: Required features not implemented
- `type_error`: Type annotation issues
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

    def __init__(self, config: ReviewAgentConfig | None = None):
        """Initialize the code review agent.

        Args:
            config: Optional configuration. If not provided, uses defaults from env.
        """
        self.config = config or ReviewAgentConfig.from_env()
        self._agent: CompiledGraph | None = None
        self._llm: ChatOpenAI | None = None

    @property
    def llm(self) -> ChatOpenAI:
        """Get the LLM instance, creating it if needed."""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.config.llm_model_name,
                temperature=self.config.temperature,
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    @property
    def agent(self) -> CompiledGraph:
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

            # Create the ReAct agent using langgraph
            self._agent = create_react_agent(
                model=self.llm,
                tools=tools,
                state_modifier=SYSTEM_PROMPT,
            )

        return self._agent

    def review(
        self,
        spec_path: str,
        code_root: str,
        module_name: str,
        component_docs: str | None = None,
    ) -> ReviewResult:
        """Run a code review.

        Args:
            spec_path: Path to the specification file.
            code_root: Root directory of the generated code.
            module_name: Name of the module being reviewed.
            component_docs: Optional documentation of external components
                used in the code (for field validation).

        Returns:
            ReviewResult containing all findings.
        """
        # Reset state for new review
        reset_review_state()

        # Build the review request message
        request_parts = [
            f"Please review the generated code for module '{module_name}'.",
            "",
            f"**Specification file:** {spec_path}",
            f"**Code root directory:** {code_root}",
            "",
            "Steps to follow:",
            "1. Read the specification file to understand requirements",
            "2. Find and analyze all Python files in the code directory",
            "3. Validate implementations against the specification",
            "4. Check test quality and coverage",
            "5. Report all issues found using create_review_comment_tool",
            "6. Call finalize_review_tool when complete",
        ]

        if component_docs:
            request_parts.extend([
                "",
                "**Component Documentation:**",
                "Use this to validate field accesses on external objects:",
                component_docs,
            ])

        request = "\n".join(request_parts)

        # Run the agent
        result = self.agent.invoke({
            "messages": [HumanMessage(content=request)],
        })

        # Extract the final review result from the last message
        messages = result.get("messages", [])

        # Look for the finalize_review_tool result
        review_result = None
        for msg in reversed(messages):
            if hasattr(msg, "content") and isinstance(msg.content, str):
                if '"module_name"' in msg.content and '"passed"' in msg.content:
                    try:
                        data = json.loads(msg.content)
                        review_result = data
                        break
                    except json.JSONDecodeError:
                        pass

        # If no proper result, construct one from collected comments
        if not review_result:
            comments = get_review_comments()
            error_count = sum(1 for c in comments if c.get("severity") == "error")

            review_result = {
                "module_name": module_name,
                "passed": error_count == 0,
                "summary": "Review completed",
                "comments": comments,
                "files_reviewed": get_files_reviewed(),
            }

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

        return ReviewResult(
            module_name=review_result.get("module_name", module_name),
            passed=review_result.get("passed", len([c for c in comments if c.severity == "error"]) == 0),
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
        for event in self.agent.stream(
            {"messages": [HumanMessage(content=request)]},
            stream_mode="values",
        ):
            messages = event.get("messages", [])
            if messages:
                last_msg = messages[-1]

                # Determine event type based on message
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    for tc in last_msg.tool_calls:
                        yield ("tool_call", f"Calling: {tc['name']}")
                elif hasattr(last_msg, "content"):
                    content = last_msg.content
                    if isinstance(content, str):
                        if '"module_name"' in content and '"passed"' in content:
                            yield ("final", content)
                        else:
                            yield ("thought", content)


def create_review_agent(config: ReviewAgentConfig | None = None) -> CodeReviewAgent:
    """Factory function to create a code review agent.

    Args:
        config: Optional configuration for the agent.

    Returns:
        Configured CodeReviewAgent instance.
    """
    return CodeReviewAgent(config)


def run_review(
    spec_path: str,
    code_root: str,
    module_name: str,
    config: ReviewAgentConfig | None = None,
    component_docs: str | None = None,
) -> ReviewResult:
    """Convenience function to run a code review.

    Args:
        spec_path: Path to the specification file.
        code_root: Root directory of the generated code.
        module_name: Name of the module being reviewed.
        config: Optional configuration for the agent.
        component_docs: Optional documentation of external components.

    Returns:
        ReviewResult containing all findings.
    """
    agent = create_review_agent(config)
    return agent.review(spec_path, code_root, module_name, component_docs)
