"""Tools for the pattern scout ReAct agent (design pattern identification)."""

from typing import Any

from langchain_core.tools import StructuredTool

from openhands.agenthub.langgraph_reviewer_agent.models import (
    IdentifiedPattern,
    PatternIdentificationOutput,
)


def make_report_patterns_tool(result_holder: dict[str, Any]) -> StructuredTool:
    """Create a report_patterns tool that validates with Pydantic and stores result.

    The tool accepts the same schema as PatternIdentificationOutput so the LLM
    produces structured output that we validate. Validated output is stored in
    result_holder["pattern_output"] for the caller to read after the agent run.

    Args:
        result_holder: Mutable dict; on success we set result_holder["pattern_output"]
                      to the validated PatternIdentificationOutput.

    Returns:
        A StructuredTool that the pattern scout agent calls when done.
    """

    def report_patterns_impl(patterns: list[dict[str, Any] | IdentifiedPattern]) -> str:
        try:
            validated_list = [
                p if isinstance(p, IdentifiedPattern) else IdentifiedPattern.model_validate(p)
                for p in patterns
            ]
            out = PatternIdentificationOutput(patterns=validated_list)
            result_holder["pattern_output"] = out
            return f"Successfully reported {len(validated_list)} design pattern(s)."
        except Exception as e:
            return (
                f"Validation error: {e}. "
                "Each pattern must have: pattern_name (str), file_path (str), "
                "line_numbers (list of int), class_or_function_names (list of str), rationale (str)."
            )

    return StructuredTool.from_function(
        func=report_patterns_impl,
        name="report_patterns_tool",
        description=(
            "Call this ONLY when you have finished reading the codebase and identified "
            "all design patterns. Provide the complete list of patterns. Each pattern must have: "
            "pattern_name (canonical name, e.g. Singleton, Factory), file_path (full path to file), "
            "line_numbers (list of relevant line numbers), class_or_function_names (list of class/function names), "
            "rationale (brief explanation of why this pattern is used here). "
            "If no design patterns are found, call with an empty list."
        ),
        args_schema=PatternIdentificationOutput,
    )
