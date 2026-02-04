"""Integration utilities for using the review agent in pipelines.

This module provides functions to integrate the code review agent
with the main OpenHands coding agent pipeline.
"""

import json
from pathlib import Path
from typing import Any

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import ReviewResult


def generate_cr_feedback_for_agent(
    spec_path: str,
    code_root: str,
    module_name: str,
    config: ReviewAgentConfig | None = None,
    component_docs: str | None = None,
) -> str:
    """Generate code review feedback formatted for the coding agent.

    This function runs a code review and returns the feedback in a format
    that can be directly passed to the coding agent as user input.

    Args:
        spec_path: Path to the specification file.
        code_root: Root directory of the generated code.
        module_name: Name of the module being reviewed.
        config: Optional configuration for the review agent.
        component_docs: Optional documentation of external components.

    Returns:
        Formatted feedback string suitable for the coding agent.
    """
    from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent

    agent = StructuredCodeReviewAgent(config=config)
    result = agent.review(
        spec_path=spec_path,
        code_root=code_root,
        module_names=[module_name] if module_name else None,
        component_docs=component_docs,
    )
    return result.to_cr_feedback()


def quick_review(
    code_root: str,
    module_name: str | None = None,
) -> dict[str, Any]:
    """Perform a quick code review without full agent loop.

    Uses just the validation tools directly without LLM reasoning.
    Useful for fast, deterministic checks.

    Args:
        code_root: Root directory of the code to review.
        module_name: Optional module name for filtering.

    Returns:
        Dictionary with review results.
    """
    from openhands.agenthub.langgraph_reviewer_agent.tools.file_tools import (
        find_python_files_tool,
    )
    from openhands.agenthub.langgraph_reviewer_agent.tools.code_analyzer import (
        extract_signatures_tool,
        extract_test_info_tool,
    )

    results = {
        "module_name": module_name or Path(code_root).name,
        "issues": [],
        "files_analyzed": [],
        "statistics": {},
    }

    # Find Python files
    files_result = json.loads(find_python_files_tool.invoke({"root_path": code_root}))

    if "error" in files_result:
        results["error"] = files_result["error"]
        return results

    source_files = files_result.get("source_files", [])
    test_files = files_result.get("test_files", [])

    results["statistics"]["source_files"] = len(source_files)
    results["statistics"]["test_files"] = len(test_files)

    # Analyze source files
    all_classes = []
    all_methods = []

    for source_file in source_files:
        file_path = source_file["path"]
        results["files_analyzed"].append(source_file["relative_path"])

        sig_result = json.loads(extract_signatures_tool.invoke({"file_path": file_path}))

        if "error" not in sig_result:
            for cls in sig_result.get("classes", []):
                all_classes.append(cls["name"])
                all_methods.extend([m["name"] for m in cls.get("methods", [])])

    results["statistics"]["classes"] = len(all_classes)
    results["statistics"]["methods"] = len(all_methods)

    # Analyze test files
    total_tests = 0
    total_assertions = 0
    uses_mocking = False

    for test_file in test_files:
        file_path = test_file["path"]
        results["files_analyzed"].append(test_file["relative_path"])

        test_result = json.loads(extract_test_info_tool.invoke({"test_file_path": file_path}))

        if "error" not in test_result:
            total_tests += test_result.get("test_count", 0)
            total_assertions += test_result.get("assertion_count", 0)
            if test_result.get("uses_mocking"):
                uses_mocking = True

    results["statistics"]["tests"] = total_tests
    results["statistics"]["assertions"] = total_assertions
    results["statistics"]["uses_mocking"] = uses_mocking

    # Basic checks
    if total_tests == 0 and test_files:
        results["issues"].append({
            "type": "warning",
            "message": "No test functions found in test files",
        })

    if total_assertions < total_tests:
        results["issues"].append({
            "type": "warning",
            "message": f"Low assertion count ({total_assertions}) for {total_tests} tests",
        })

    results["passed"] = len([i for i in results["issues"] if i["type"] == "error"]) == 0

    return results


class CodeReviewRunner:
    """Runner for integrating code review into pipelines.

    This class provides a stateful interface for running code reviews
    and tracking results across multiple review sessions.
    """

    def __init__(self, config: ReviewAgentConfig | None = None):
        """Initialize the runner.

        Args:
            config: Optional configuration for the review agent.
        """
        self.config = config or ReviewAgentConfig.from_env()
        self._agent = None
        self.review_history: list[ReviewResult] = []

    @property
    def agent(self):
        """Get the agent instance, creating it if needed."""
        if self._agent is None:
            from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent
            self._agent = StructuredCodeReviewAgent(self.config)
        return self._agent

    def run_review(
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
            component_docs: Optional documentation of external components.

        Returns:
            ReviewResult containing all findings.
        """
        result = self.agent.review(
            spec_path=spec_path,
            code_root=code_root,
            module_names=[module_name] if module_name else None,
            component_docs=component_docs,
        )

        self.review_history.append(result)
        return result

    def get_feedback(self, result: ReviewResult | None = None) -> str:
        """Get feedback from the last or specified review.

        Args:
            result: Optional specific result. Uses last result if not provided.

        Returns:
            Formatted feedback string.
        """
        if result is None:
            if not self.review_history:
                return "No reviews have been run yet."
            result = self.review_history[-1]

        return result.to_cr_feedback()

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all reviews run.

        Returns:
            Dictionary with summary statistics.
        """
        if not self.review_history:
            return {"total_reviews": 0}

        return {
            "total_reviews": len(self.review_history),
            "passed": sum(1 for r in self.review_history if r.passed),
            "failed": sum(1 for r in self.review_history if not r.passed),
            "total_errors": sum(r.error_count for r in self.review_history),
            "total_warnings": sum(r.warning_count for r in self.review_history),
            "modules_reviewed": [r.module_name for r in self.review_history],
        }

    def clear_history(self):
        """Clear the review history."""
        self.review_history = []
