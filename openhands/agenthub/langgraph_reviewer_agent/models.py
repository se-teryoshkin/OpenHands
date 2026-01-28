"""Data models for the Code Review Agent."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    """Severity levels for review issues."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    """Categories of review issues based on common mistakes analysis."""
    SIGNATURE_MISMATCH = "signature_mismatch"
    FIELD_ACCESS_ERROR = "field_access_error"
    MAPPING_INCOMPLETE = "mapping_incomplete"
    FIELD_MAPPING_ERROR = "field_mapping_error"
    TEST_QUALITY = "test_quality"
    STRUCTURE_ISSUE = "structure_issue"
    MISSING_IMPLEMENTATION = "missing_implementation"
    TYPE_ERROR = "type_error"
    PYDANTIC_ISSUE = "pydantic_issue"
    CODE_QUALITY_ISSUE = "code_quality_issue"  # Bad exception handling, mocks in prod, etc.
    GUIDELINE_VIOLATION = "guideline_violation"  # Violates coding guidelines
    SCOPE_VIOLATION = "scope_violation"  # Module implements out-of-scope functionality
    DATA_STRUCTURE_MISMATCH = "data_structure_mismatch"  # Model doesn't match API definition
    GENERAL = "general"


class ReviewComment(BaseModel):
    """A single review comment/issue."""

    category: IssueCategory = Field(
        description="Category of the issue"
    )
    severity: IssueSeverity = Field(
        description="Severity level of the issue"
    )
    file_path: str = Field(
        description="Path to the file with the issue"
    )
    line_number: Optional[int] = Field(
        default=None,
        description="Line number where the issue occurs"
    )
    message: str = Field(
        description="Description of the issue"
    )
    suggestion: Optional[str] = Field(
        default=None,
        description="Suggested fix for the issue"
    )
    spec_reference: Optional[str] = Field(
        default=None,
        description="Reference to the specification that was violated"
    )

    def to_markdown(self) -> str:
        """Format as markdown comment."""
        severity_emoji = {
            IssueSeverity.ERROR: "🔴",
            IssueSeverity.WARNING: "🟡",
            IssueSeverity.INFO: "🔵",
        }
        emoji = severity_emoji.get(self.severity, "⚪")

        lines = [
            f"{emoji} **[{self.severity.value.upper()}]** {self.message}",
            f"   - File: `{self.file_path}`",
        ]

        if self.line_number:
            lines.append(f"   - Line: {self.line_number}")

        if self.spec_reference:
            lines.append(f"   - Spec: {self.spec_reference}")

        if self.suggestion:
            lines.append(f"   - 💡 Suggestion: {self.suggestion}")

        return "\n".join(lines)


class ReviewResult(BaseModel):
    """Complete result of a code review."""

    passed: bool = Field(
        description="Whether the review passed (no errors)"
    )
    comments: list[ReviewComment] = Field(
        default_factory=list,
        description="List of review comments"
    )
    summary: str = Field(
        default="",
        description="Summary of the review"
    )
    files_reviewed: list[str] = Field(
        default_factory=list,
        description="List of files that were reviewed"
    )

    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for c in self.comments if c.severity == IssueSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for c in self.comments if c.severity == IssueSeverity.WARNING)

    @property
    def info_count(self) -> int:
        """Count of info-level issues."""
        return sum(1 for c in self.comments if c.severity == IssueSeverity.INFO)

    def to_markdown(self) -> str:
        """Format the entire review as markdown."""
        status = "✅ PASSED" if self.passed else "❌ FAILED"

        lines = [
            f"# Code Review",
            f"**Status:** {status}",
            "",
            f"**Summary:** {self.summary}",
            "",
            f"**Statistics:**",
            f"- 🔴 Errors: {self.error_count}",
            f"- 🟡 Warnings: {self.warning_count}",
            f"- 🔵 Info: {self.info_count}",
            "",
        ]

        if self.files_reviewed:
            lines.append("**Files Reviewed:**")
            for f in self.files_reviewed:
                lines.append(f"- `{f}`")
            lines.append("")

        if self.comments:
            lines.append("## Issues")
            lines.append("")

            # Group by category
            by_category: dict[IssueCategory, list[ReviewComment]] = {}
            for comment in self.comments:
                if comment.category not in by_category:
                    by_category[comment.category] = []
                by_category[comment.category].append(comment)

            for category, comments in by_category.items():
                lines.append(f"### {category.value.replace('_', ' ').title()}")
                lines.append("")
                for comment in comments:
                    lines.append(comment.to_markdown())
                    lines.append("")
        else:
            lines.append("No issues found. 🎉")

        return "\n".join(lines)

    def to_cr_feedback(self) -> str:
        """Format as code review feedback for the coding agent."""
        if self.passed:
            return f"Code review passed. {self.summary}"

        lines = [
            "Code review feedback:",
            "",
            self.summary,
            "",
            "Please address the following issues:",
            "",
        ]

        for i, comment in enumerate(self.comments, 1):
            lines.append(f"{i}. [{comment.severity.value.upper()}] {comment.message}")
            if comment.file_path:
                lines.append(f"   File: {comment.file_path}")
            if comment.line_number:
                lines.append(f"   Line: {comment.line_number}")
            if comment.suggestion:
                lines.append(f"   Suggestion: {comment.suggestion}")
            lines.append("")

        return "\n".join(lines)


class SignatureInfo(BaseModel):
    """Extracted signature information."""

    class_name: str
    method_name: str
    parameters: list[dict]  # [{"name": "x", "type": "int"}]
    return_type: Optional[str] = None
    line_number: int = 0


class FieldInfo(BaseModel):
    """Extracted field information."""

    class_name: str
    field_name: str
    field_type: Optional[str] = None
    is_optional: bool = False
    default_value: Optional[str] = None


class TestInfo(BaseModel):
    """Information about test coverage."""

    test_file: str
    test_functions: list[str]
    mocked_objects: list[str]
    assertions_count: int
    covered_methods: list[str]
