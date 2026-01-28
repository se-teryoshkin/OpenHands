"""Reporting tools for the code review agent."""

import json
from typing import Any

from langchain_core.tools import tool

# Global storage for review comments during a session
_review_comments: list[dict] = []
_files_reviewed: list[str] = []


def reset_review_state():
    """Reset the review state for a new review session."""
    global _review_comments, _files_reviewed
    _review_comments = []
    _files_reviewed = []


def get_review_comments() -> list[dict]:
    """Get the current list of review comments."""
    return _review_comments.copy()


def get_files_reviewed() -> list[str]:
    """Get the list of files reviewed."""
    return list(set(_files_reviewed))


@tool
def create_review_comment_tool(
    category: str,
    severity: str,
    file_path: str,
    message: str,
    line_number: int | None = None,
    suggestion: str | None = None,
    spec_reference: str | None = None
) -> str:
    """Create a review comment for an issue found during code review.

    Call this tool for each issue found during the review process.

    Args:
        category: Issue category. One of: "signature_mismatch", "field_access_error",
            "mapping_incomplete", "field_mapping_error", "test_quality", "structure_issue",
            "missing_implementation", "type_error", "pydantic_issue", "general".
        severity: Severity level. One of: "error", "warning", "info".
        file_path: Path to the file with the issue.
        message: Description of the issue.
        line_number: Optional line number where the issue occurs.
        suggestion: Optional suggested fix for the issue.
        spec_reference: Optional reference to the specification that was violated.

    Returns:
        Confirmation that the comment was recorded.
    """
    global _review_comments, _files_reviewed

    valid_categories = [
        "signature_mismatch", "field_access_error", "mapping_incomplete",
        "field_mapping_error", "test_quality", "structure_issue",
        "missing_implementation", "type_error", "pydantic_issue",
        "code_quality_issue", "guideline_violation", "scope_violation",
        "data_structure_mismatch", "general"
    ]

    valid_severities = ["error", "warning", "info"]

    if category not in valid_categories:
        return f"Error: Invalid category '{category}'. Must be one of: {valid_categories}"

    if severity not in valid_severities:
        return f"Error: Invalid severity '{severity}'. Must be one of: {valid_severities}"

    comment = {
        "category": category,
        "severity": severity,
        "file_path": file_path,
        "message": message,
    }

    if line_number is not None:
        comment["line_number"] = str(line_number)

    if suggestion:
        comment["suggestion"] = suggestion

    if spec_reference:
        comment["spec_reference"] = spec_reference

    # Deduplication: Check if similar comment already exists
    # Comments are considered duplicates if they have the same message core
    # (ignoring minor variations) and same file
    message_core = message.lower().strip()[:100]  # First 100 chars for comparison
    for existing in _review_comments:
        existing_core = existing.get("message", "").lower().strip()[:100]
        if (existing_core == message_core and
            existing.get("file_path") == file_path and
            existing.get("category") == category):
            return f"Duplicate comment skipped: [{severity.upper()}] {message[:50]}... (already recorded)"

    _review_comments.append(comment)
    _files_reviewed.append(file_path)

    return f"Review comment recorded: [{severity.upper()}] {message} in {file_path}"


@tool
def finalize_review_tool(
    module_name: str,
    summary: str,
    passed: bool | None = None
) -> str:
    """Finalize the review and generate the complete review result.

    Call this tool when the review is complete to generate the final report.

    Args:
        module_name: Name of the reviewed module.
        summary: Summary of the review findings.
        passed: Whether the review passed. If None, determined automatically
            based on whether there are any error-level issues.

    Returns:
        JSON string containing the complete review result.
    """
    global _review_comments, _files_reviewed

    error_count = sum(1 for c in _review_comments if c.get("severity") == "error")
    warning_count = sum(1 for c in _review_comments if c.get("severity") == "warning")
    info_count = sum(1 for c in _review_comments if c.get("severity") == "info")

    # Determine pass/fail if not specified
    if passed is None:
        passed = error_count == 0

    result = {
        "module_name": module_name,
        "passed": passed,
        "summary": summary,
        "comments": _review_comments,
        "files_reviewed": list(set(_files_reviewed)),
        "statistics": {
            "errors": error_count,
            "warnings": warning_count,
            "info": info_count,
            "total": len(_review_comments),
        },
    }

    # Generate markdown report
    status = "✅ PASSED" if passed else "❌ FAILED"
    markdown_lines = [
        f"# Code Review: {module_name}",
        f"**Status:** {status}",
        "",
        f"**Summary:** {summary}",
        "",
        "**Statistics:**",
        f"- 🔴 Errors: {error_count}",
        f"- 🟡 Warnings: {warning_count}",
        f"- 🔵 Info: {info_count}",
        "",
    ]

    if _files_reviewed:
        markdown_lines.append("**Files Reviewed:**")
        for f in sorted(set(_files_reviewed)):
            markdown_lines.append(f"- `{f}`")
        markdown_lines.append("")

    if _review_comments:
        markdown_lines.append("## Issues")
        markdown_lines.append("")

        # Group by category
        by_category: dict[str, list[dict]] = {}
        for comment in _review_comments:
            cat = comment.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(comment)

        severity_emoji = {"error": "🔴", "warning": "🟡", "info": "🔵"}

        for category, comments in by_category.items():
            markdown_lines.append(f"### {category.replace('_', ' ').title()}")
            markdown_lines.append("")
            for comment in comments:
                emoji = severity_emoji.get(comment.get("severity", "info"), "⚪")
                markdown_lines.append(
                    f"{emoji} **[{comment.get('severity', 'info').upper()}]** "
                    f"{comment.get('message', '')}"
                )
                markdown_lines.append(f"   - File: `{comment.get('file_path', 'unknown')}`")
                if comment.get("line_number"):
                    markdown_lines.append(f"   - Line: {comment.get('line_number')}")
                if comment.get("suggestion"):
                    markdown_lines.append(f"   - 💡 Suggestion: {comment.get('suggestion')}")
                markdown_lines.append("")
    else:
        markdown_lines.append("No issues found. 🎉")

    result["markdown_report"] = "\n".join(markdown_lines)

    return json.dumps(result, indent=2)
