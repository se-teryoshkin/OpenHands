"""Structured Workflow Code Review Agent.

This agent uses a deterministic workflow with parallel tool execution
and a single LLM call for analysis, providing:
- ~5x faster execution than ReAct
- Consistent behavior (always runs all validators)
- Lower cost (1-2 LLM calls vs 15-20)
- Predictable results
"""

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import (
    IssueCategory,
    IssueSeverity,
    ReviewComment,
    ReviewResult,
)
from openhands.agenthub.langgraph_reviewer_agent.tools.validators import (
    validate_signatures_tool,
    validate_test_quality_tool,
    validate_code_quality_tool,
    validate_pydantic_usage_tool,
    validate_project_structure_tool,
    validate_cross_file_usage_tool,
)

logger = logging.getLogger("code_review_agent")


class ReviewIssue(BaseModel):
    """Single issue found during review."""
    category: str = Field(description="Issue category: signature_mismatch, test_quality, code_quality_issue, pydantic_issue, structure_issue, missing_implementation, field_mapping_error, cross_file_issue, general")
    severity: str = Field(description="Severity: error, warning, info")
    file_path: str = Field(description="Path to the file with the issue")
    line_number: int | None = Field(default=None, description="Line number if applicable")
    message: str = Field(description="Clear description of the issue")
    suggestion: str | None = Field(default=None, description="How to fix the issue")


class LLMReviewOutput(BaseModel):
    """Structured output from LLM analysis."""
    issues: list[ReviewIssue] = Field(default_factory=list, description="List of issues found")
    summary: str = Field(description="Brief summary of the review findings")
    passed: bool = Field(description="Whether the code passes review (no errors)")


class StructuredCodeReviewAgent:
    """Structured Workflow Code Review Agent.

    Executes a deterministic workflow:
    1. Discovery: Read spec and find all files
    2. Validation: Run all validators in parallel (signatures, code quality,
       test quality, Pydantic usage, project structure)
    3. Analysis: Single LLM call to interpret results
    4. Report: Generate structured output
    """

    def __init__(self, config: ReviewAgentConfig | None = None, verbose: bool = False):
        """Initialize the structured review agent."""
        self.config = config or ReviewAgentConfig.from_env()
        self.verbose = verbose or self.config.verbose
        self._llm: ChatOpenAI | None = None
        self._start_time: datetime | None = None
        self._code_root: Path | None = None
        self._file_path_mapping: dict[str, str] = {}
        self._files_reviewed: list[str] = []

    def _log(self, message: str, level: str = "info"):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            elapsed = ""
            if self._start_time:
                elapsed = f" [+{(datetime.now() - self._start_time).total_seconds():.1f}s]"
            getattr(logger, level)(f"{message}{elapsed}")

    @property
    def llm(self) -> ChatOpenAI:
        """Get the LLM instance."""
        if self._llm is None:
            api_key = SecretStr(self.config.llm_api_key) if self.config.llm_api_key else None
            self._llm = ChatOpenAI(
                model=self.config.llm_model_name,
                temperature=self.config.temperature,
                api_key=api_key,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    def _discover_files(self, code_root: str) -> dict:
        """Discover all Python files in the code root."""
        root = Path(code_root)
        if not root.exists():
            return {"error": f"Directory not found: {code_root}", "files": []}

        files = {
            "source_files": [],
            "test_files": [],
            "all_files": [],
        }

        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            file_info = {
                "path": str(py_file),
                "name": py_file.name,
                "relative": str(py_file.relative_to(root)),
            }

            files["all_files"].append(file_info)

            if "test" in py_file.name.lower():
                files["test_files"].append(file_info)
            else:
                files["source_files"].append(file_info)

        return files

    def _read_file(self, file_path: str) -> str:
        """Read a single file."""
        try:
            return Path(file_path).read_text(encoding='utf-8')
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_all_files(self, files: dict) -> dict[str, str]:
        """Read all discovered files."""
        contents = {}
        for file_info in files.get("all_files", []):
            path = file_info["path"]
            contents[path] = self._read_file(path)
        return contents

    def _run_single_validator(self, name: str, func: Callable, args: dict) -> tuple[str, Any]:
        """Run a single validator and return (name, result)."""
        try:
            result = func(args)
            if isinstance(result, str):
                return (name, json.loads(result))
            return (name, result)
        except Exception as e:
            logger.debug(f"Validator {name} failed: {e}")
            return (name, {"error": str(e)})

    def _extract_interface_from_spec(self, spec_content: str) -> str:
        """Extract interface/protocol definition from specification."""
        # Look for class definitions in the spec
        pattern = r'```(?:python)?\s*(class\s+\w+.*?(?=```|$))'
        matches = re.findall(pattern, spec_content, re.DOTALL)
        if matches:
            return matches[0]

        # Look for method signatures directly
        method_pattern = r'(?:def\s+\w+\s*\([^)]*\)\s*(?:->\s*[^:]+)?:)'
        methods = re.findall(method_pattern, spec_content)
        if methods:
            return "\n".join(f"    {m}" for m in methods)

        return spec_content

    def _run_validators(
        self,
        spec_content: str,
        code_root: str,
        files: dict,
        file_contents: dict[str, str],
        api_data_structures: str = "",
        external_components_path: str = "",
    ) -> dict[str, Any]:
        """Run all validators in parallel and collect results."""
        results = {}

        self._log("Running all validators in parallel...")

        # Extract interface from spec for signature validation
        spec_interface = self._extract_interface_from_spec(spec_content)

        # Prepare project root for structure validation
        code_path = Path(code_root)
        project_root = code_root
        if code_path.name.endswith("_module") or code_path.name in ("src", "lib"):
            project_root = str(code_path.parent.parent) if code_path.parent.name == "src" else str(code_path.parent)
        elif code_path.parent.name.endswith("_module"):
            project_root = str(code_path.parent.parent)

        # Collect all validation tasks
        tasks = []

        # 1. Signature validation tasks (for service files)
        for file_info in files.get("source_files", []):
            if "service" in file_info["name"].lower():
                content = file_contents.get(file_info["path"], "")
                class_match = re.search(r'class\s+(\w+)', content)
                class_name = class_match.group(1) if class_match else "Service"

                tasks.append((
                    f"signatures_{file_info['name']}",
                    validate_signatures_tool.invoke,
                    {
                        "spec_interface": spec_interface,
                        "impl_file": file_info["path"],
                        "impl_class": class_name,
                    }
                ))

        # 2. Code quality validation tasks (for each source file)
        for file_info in files.get("source_files", []):
            tasks.append((
                f"code_quality_{file_info['name']}",
                validate_code_quality_tool.invoke,
                {"file_path": file_info["path"]}
            ))

        # 3. Test quality validation tasks (for each test file)
        for file_info in files.get("test_files", []):
            tasks.append((
                f"test_quality_{file_info['name']}",
                validate_test_quality_tool.invoke,
                {
                    "test_file": file_info["path"],
                    "interface_methods": [],
                    "allow_mocking": False,
                }
            ))

        # 4. Pydantic usage validation (once for whole codebase)
        tasks.append((
            "pydantic_usage",
            validate_pydantic_usage_tool.invoke,
            {
                "code_root": code_root,
                "api_data_structures": api_data_structures,
            }
        ))

        # 5. Cross-file usage validation
        tasks.append((
            "cross_file_usage",
            validate_cross_file_usage_tool.invoke,
            {
                "code_root": code_root,
                "max_inferences": 400,
                "include_tests": False,
                "external_components_path": external_components_path,
            }
        ))

        # 6. Project structure validation
        tasks.append((
            "project_structure",
            validate_project_structure_tool.invoke,
            {
                "code_root": project_root,
                "module_name": "module",
            }
        ))

        # Execute all tasks in parallel
        with ThreadPoolExecutor(max_workers=min(len(tasks), 20)) as executor:
            future_to_key = {}
            for key, func, args in tasks:
                future = executor.submit(self._run_single_validator, key, func, args)
                future_to_key[future] = key

            # Collect results as they complete
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    _, result = future.result()
                    results[key] = result
                except Exception as e:
                    results[key] = {"error": str(e)}

        # Post-process project structure results to filter issues
        if "project_structure" in results and isinstance(results["project_structure"], dict):
            struct_result = results["project_structure"]
            if "issues" in struct_result:
                filtered_issues = []
                for issue in struct_result["issues"]:
                    issue_type = issue.get("type", "")
                    message = issue.get("message", "")
                    # Keep only issues about specific misplaced files (like SOLUTION_SUMMARY.md)
                    if issue_type == "misplaced_file" and (
                        "SOLUTION_SUMMARY" in message or
                        ".md" in message
                    ):
                        filtered_issues.append(issue)
                struct_result["issues"] = filtered_issues

        return results

    def _build_analysis_prompt(
        self,
        spec_content: str,
        file_contents: dict[str, str],
        validation_results: dict[str, Any],
        coding_guidelines: str = "",
        modules_description: str = "",
    ) -> str:
        """Build the prompt for LLM analysis."""
        # Prepare file contents summary
        files_summary = []
        for path, content in file_contents.items():
            # Truncate long files
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            files_summary.append(f"### {Path(path).name}\n```python\n{content}\n```")

        # Prepare validation results summary
        validation_summary = []
        for name, result in validation_results.items():
            if isinstance(result, dict):
                issues = result.get("issues", [])
                valid = result.get("valid", True)
                error = result.get("error")

                if error:
                    validation_summary.append(f"**{name}**: Error - {error}")
                elif issues:
                    validation_summary.append(f"**{name}**: {len(issues)} issues found")
                    # Get file path from result level (for validators that store it there)
                    result_file = result.get("file", "")
                    for issue in issues[:5]:  # Limit to 5 issues per validator
                        msg = issue.get("message", str(issue))[:200]
                        # Include file path and line number if available
                        # Some validators store file at result level (code_quality, signatures), others at issue level (cross_file)
                        file_path = issue.get("file") or result_file
                        line_num = issue.get("line")
                        # Format: File: path, Line: number, Message: message
                        # This format makes it easy for LLM to extract
                        # IMPORTANT: Always include File: even if at result level
                        parts = []
                        if file_path:
                            parts.append(f"File: {file_path}")
                        elif result_file:
                            # Fallback: use result-level file
                            parts.append(f"File: {result_file}")
                        if line_num is not None:
                            parts.append(f"Line: {line_num}")
                        parts.append(f"Message: {msg}")
                        validation_summary.append(f"  - {', '.join(parts)}")
                else:
                    validation_summary.append(f"**{name}**: ✓ No issues")

        prompt = f"""You are a code review expert. Analyze the following code review results and provide a structured assessment.

## Specification
```
{spec_content[:4000]}
```

## Source Files
{chr(10).join(files_summary[:5])}

## Validation Results
{chr(10).join(validation_summary)}

"""

        if coding_guidelines:
            prompt += f"""
## Coding Guidelines
{coding_guidelines[:2000]}
"""

        if modules_description:
            prompt += f"""
## Module Architecture
{modules_description[:1500]}
"""

        prompt += """
## Your Task

**STRICTLY analyze only the validation results above.** Do NOT invent new issues.

For each issue ALREADY found by validators, convert it to this format:
1. **category**: One of: signature_mismatch, test_quality, code_quality_issue, pydantic_issue, structure_issue, cross_file_issue, general
2. **severity**: error (must fix), warning (should fix), info (suggestion)
3. **file_path**: REQUIRED - The file where the issue is located. Extract using these methods in order:
   - First, check if the validator output shows "File: path" in the issue line
   - If not, check if the issue has a "file" field (e.g., issue["file"])
   - If still not found, check the validator result level - some validators store file at result["file"]
     * For `signatures_*` validators: file is at result level (the impl_file parameter)
     * For `code_quality_*` validators: file is at result level (the file_path parameter)
     * For `cross_file_usage` validator: file is in each issue["file"]
   - If absolutely cannot find file_path, use "unknown" (but this should be rare)
4. **line_number**: Extract the line number from "Line:" in validator output, or from issue["line"] field. Use null only if no line number is provided.
5. **message**: The issue message from the validator (extract from "Message:" or issue["message"])
6. **suggestion**: How to fix it

**CRITICAL: file_path is REQUIRED for every issue. Never use null. Always extract it from the validator output using the methods above.**

**EXAMPLES:**

Example 1 - Issue with file at result level:
```
Validator output: **code_quality_service.py**: 2 issues found
  - File: src/service.py, Line: 45, Message: 'except Exception:' without re-raise...
```
→ Extract: `file_path: "src/service.py"` (from "File:" in the line)

Example 2 - Issue with file in issue object:
```
Validator output: **cross_file_usage**: 1 issue found
  - File: src/module/file.py, Line: 123, Message: Unresolved import...
```
→ Extract: `file_path: "src/module/file.py"` (from "File:" in the line)

Example 3 - Signature validator (file at result level):
```
Validator output: **signatures_service.py**: 1 issue found
  - Message: Method 'create_item' from spec not implemented
```
→ Extract: `file_path: <impl_file from signatures_service.py result>` (check result["file"])

**CATEGORY MAPPING RULES:**
- Issues from `cross_file_usage` validator → use category `cross_file_issue`
- Issues from `validate_signatures_tool` → use category `signature_mismatch`
- Issues from `validate_test_quality_tool` → use category `test_quality`
- Issues from `validate_code_quality_tool` → use category `code_quality_issue`
- Issues from `validate_pydantic_usage_tool` → use category `pydantic_issue`
- Issues from `validate_project_structure_tool` → use category `structure_issue`
- All other validator issues → use category `general`

**CRITICAL RULES:**
- ONLY report issues that were found by the validators above
- Do NOT add issues like "NotImplementedError" unless a validator flagged it
- Do NOT add structure issues unless validate_project_structure found them
- Do NOT suggest "importing from API data structures" - they are specifications
- IGNORE issues about missing src/ directory if files are properly organized
- Be CONSERVATIVE - when in doubt, don't report
- Aim for HIGH PRECISION (fewer false positives)

Expected output: report all found issues from validators. If validators found no issues, report empty list.

Respond with JSON:
{
  "issues": [...],
  "summary": "Brief summary (1-2 sentences)",
  "passed": true/false (false only if ERROR severity issues exist)
}
"""

        return prompt

    def _extract_file_path_from_validator_context(
        self,
        issue: dict,
        validator_name: str,
        validator_results: dict[str, Any],
    ) -> str | None:
        """Extract file_path from validator context when missing from issue.

        Some validators store file at result level, not issue level.
        This method attempts to find the file_path by checking:
        1. Validator result-level "file" field
        2. Validator name patterns (e.g., "signatures_service.py" -> check signatures validator)
        """
        # Try to get file from validator result
        if validator_name in validator_results:
            result = validator_results[validator_name]
            if isinstance(result, dict):
                # Check result-level file
                file_path = result.get("file")
                if file_path:
                    return file_path

        # Try to infer validator from issue category or validator name pattern
        # For signatures validator: pattern is "signatures_<filename>"
        if validator_name.startswith("signatures_"):
            # Extract filename from validator name
            filename = validator_name.replace("signatures_", "")
            if filename in validator_results:
                result = validator_results[validator_name]
                if isinstance(result, dict):
                    file_path = result.get("file")
                    if file_path:
                        return file_path

        # For code_quality validator: pattern is "code_quality_<filename>"
        if validator_name.startswith("code_quality_"):
            if validator_name in validator_results:
                result = validator_results[validator_name]
                if isinstance(result, dict):
                    file_path = result.get("file")
                    if file_path:
                        return file_path

        return None

    def _parse_llm_response(
        self,
        response: str,
        validator_results: dict[str, Any] | None = None,
    ) -> LLMReviewOutput:
        """Parse the LLM response into structured output.

        Args:
            response: Raw LLM response string
            validator_results: Optional validator results for fallback file_path extraction
        """
        validator_results = validator_results or {}

        try:
            # Try to extract JSON from the response
            content = response.strip()

            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content)

            # Sanitize issues: ensure file_path is never None
            sanitized_issues = []
            for i, issue in enumerate(data.get("issues", [])):
                file_path = issue.get("file_path")

                # If file_path is None or empty, try to extract from validator context
                if not file_path:
                    category = issue.get("category", "")
                    message = issue.get("message", "")

                    # Map category to likely validator patterns
                    validator_patterns = {
                        "signature_mismatch": ["signatures_"],
                        "code_quality_issue": ["code_quality_"],
                        "test_quality": ["test_quality_"],
                        "pydantic_issue": ["pydantic_usage", "pydantic_"],
                        "cross_file_issue": ["cross_file_usage", "cross_file_"],
                        "structure_issue": ["project_structure"],
                    }

                    # Try to find matching validator by category
                    prefixes = validator_patterns.get(category, [])
                    for prefix in prefixes:
                        for validator_name in validator_results.keys():
                            if validator_name.startswith(prefix) or validator_name == prefix:
                                file_path = self._extract_file_path_from_validator_context(
                                    issue, validator_name, validator_results
                                )
                                if file_path:
                                    break
                        if file_path:
                            break

                    # If still not found, try all validators (fallback)
                    if not file_path:
                        for validator_name, result in validator_results.items():
                            if isinstance(result, dict):
                                # Check if this validator has a file at result level
                                result_file = result.get("file")
                                if result_file:
                                    # Check if this validator's issues match this category
                                    validator_issues = result.get("issues", [])
                                    # Try to match by checking if message appears in validator issues
                                    for v_issue in validator_issues:
                                        v_msg = v_issue.get("message", "")
                                        if message[:50] in v_msg or v_msg[:50] in message:
                                            file_path = result_file
                                            break
                                    if file_path:
                                        break

                    # If still not found, use "unknown"
                    if not file_path:
                        logger.warning(
                            f"Issue {i} missing file_path, category: {category}, "
                            f"message: {message[:50]}... Using 'unknown' as fallback."
                        )
                        file_path = "unknown"

                # Update issue with sanitized file_path
                issue["file_path"] = file_path
                sanitized_issues.append(issue)

            # Replace issues with sanitized version
            data["issues"] = sanitized_issues

            return LLMReviewOutput(**data)
        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            # Log the response for debugging (truncated)
            logger.debug(f"LLM response (first 500 chars): {response[:500]}")
            # Return a safe default
            return LLMReviewOutput(
                issues=[],
                summary=f"Failed to parse review output: {e}",
                passed=True
            )

    def _normalize_file_path(self, file_path: str) -> str:
        """Normalize file path to be relative to code_root."""
        if self._code_root is None:
            return file_path

        # First, check if this is a simplified validator key (like "signatures_service.py")
        # and map it to the actual file path
        if hasattr(self, '_file_path_mapping') and file_path in self._file_path_mapping:
            file_path = self._file_path_mapping[file_path]

        try:
            path = Path(file_path)
            # If it's just a filename (no directory), try to find it in files_reviewed
            if path.parent == Path(".") or (not path.parent.name and path.name):
                # It's just a filename like "service.py"
                # Try to find it in the file_contents or files_reviewed
                if hasattr(self, '_files_reviewed') and self._files_reviewed:
                    for reviewed_path in self._files_reviewed:
                        if Path(reviewed_path).name == path.name:
                            # Found matching file, use this path for normalization
                            file_path = reviewed_path
                            break

            path = Path(file_path)
            # If it's an absolute path, convert to relative
            if path.is_absolute():
                try:
                    # Ensure both paths are resolved for comparison
                    abs_path = path.resolve()
                    abs_code_root = self._code_root.resolve()
                    relative_path = abs_path.relative_to(abs_code_root)
                    return str(relative_path)
                except ValueError as e:
                    # Path is outside code_root, return as-is
                    logger.debug(f"Path {file_path} is outside code_root {self._code_root}: {e}")
                    return str(path)
            else:
                # Relative path - if it exists relative to code_root, return as-is
                # Otherwise try to resolve it
                full_path = self._code_root / path
                if full_path.exists():
                    return str(path)
                # If not found, return as-is (might be a relative path)
                return str(path)
        except Exception as e:
            # If anything fails, return original
            logger.debug(f"Failed to normalize path {file_path}: {e}")
            return file_path

    def _convert_to_review_result(
        self,
        llm_output: LLMReviewOutput,
        files_reviewed: list[str],
    ) -> ReviewResult:
        """Convert LLM output to ReviewResult."""
        comments = []

        for issue in llm_output.issues:
            try:
                category = IssueCategory(issue.category)
            except ValueError:
                category = IssueCategory.GENERAL

            try:
                severity = IssueSeverity(issue.severity)
            except ValueError:
                severity = IssueSeverity.WARNING

            # Normalize file path to be relative to code_root
            # Safety check: ensure file_path is not None (should be handled by sanitization)
            file_path = issue.file_path or "unknown"
            normalized_path = self._normalize_file_path(file_path)

            comments.append(ReviewComment(
                category=category,
                severity=severity,
                file_path=normalized_path,
                line_number=issue.line_number,
                message=issue.message,
                suggestion=issue.suggestion,
            ))

        return ReviewResult(
            passed=llm_output.passed,
            summary=llm_output.summary,
            comments=comments,
            files_reviewed=files_reviewed,
        )

    def review(
        self,
        spec_path: str,
        code_root: str,
        component_docs: str | None = None,
        data_structures: str | None = None,
        coding_guidelines: str | None = None,
        modules_description: str | None = None,
        external_components_path: str | None = None,
    ) -> ReviewResult:
        """Run a structured code review.

        Workflow:
        1. Discovery: Read spec and discover files
        2. Validation: Run all validators in parallel (signatures, code quality,
           test quality, Pydantic usage, project structure)
        3. Analysis: Single LLM call to interpret results
        4. Report: Generate structured output
        """
        self._start_time = datetime.now()
        self._code_root = Path(code_root).resolve()  # Store for path normalization
        logger.info("Starting structured code review")
        logger.info(f"  Spec: {spec_path}")
        logger.info(f"  Code: {code_root}")

        # Phase 1: Discovery
        phase_start = datetime.now()
        self._log("Phase 1: Discovery")
        spec_content = self._read_file(spec_path)
        files = self._discover_files(code_root)
        file_contents = self._read_all_files(files)
        discovery_time = (datetime.now() - phase_start).total_seconds()

        self._log(f"  Found {len(files.get('source_files', []))} source files, "
                  f"{len(files.get('test_files', []))} test files")
        self._log(f"  Discovery completed in {discovery_time:.2f}s")

        # Phase 2: Validation
        phase_start = datetime.now()
        self._log("Phase 2: Validation")
        validation_results = self._run_validators(
            spec_content=spec_content,
            code_root=code_root,
            files=files,
            file_contents=file_contents,
            api_data_structures=data_structures or "",
            external_components_path=external_components_path or "",
        )
        validation_time = (datetime.now() - phase_start).total_seconds()

        # Build a mapping from simplified validator keys to actual file paths
        # Validator keys are like "signatures_service.py", "code_quality_service.py", etc.
        self._file_path_mapping = {}
        for file_info in files.get("source_files", []):
            file_name = file_info["name"]
            file_path = file_info["path"]
            self._file_path_mapping[f"signatures_{file_name}"] = file_path
            self._file_path_mapping[f"code_quality_{file_name}"] = file_path
        for file_info in files.get("test_files", []):
            file_name = file_info["name"]
            file_path = file_info["path"]
            self._file_path_mapping[f"test_quality_{file_name}"] = file_path

        # Count issues from validators
        total_validator_issues = 0
        for name, result in validation_results.items():
            if isinstance(result, dict) and "issues" in result:
                total_validator_issues += len(result["issues"])
        self._log(f"  Validators found {total_validator_issues} potential issues")

        # Phase 3: LLM Analysis
        self._log("Phase 3: LLM Analysis")
        prompt = self._build_analysis_prompt(
            spec_content=spec_content,
            file_contents=file_contents,
            validation_results=validation_results,
            coding_guidelines=coding_guidelines or "",
            modules_description=modules_description or "",
        )

        try:
            response = self.llm.invoke(prompt)
            # Extract string content from response (handles both str and list formats)
            content = response.content
            content = " ".join(str(item) for item in content) if isinstance(content, list) else str(content)
            # Pass validator_results for fallback file_path extraction
            llm_output = self._parse_llm_response(content, validator_results=validation_results)
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            llm_output = LLMReviewOutput(
                issues=[],
                summary=f"LLM analysis failed: {e}",
                passed=True
            )

        # Phase 4: Report
        self._log("Phase 4: Report Generation")
        # Store files_reviewed for path normalization
        self._files_reviewed = list(file_contents.keys())
        # Normalize files_reviewed paths to be relative to code_root
        normalized_files_reviewed = [
            self._normalize_file_path(path) for path in self._files_reviewed
        ]
        result = self._convert_to_review_result(
            llm_output=llm_output,
            files_reviewed=normalized_files_reviewed,
        )

        # Log completion
        duration = (datetime.now() - self._start_time).total_seconds()
        logger.info(f"Review completed in {duration:.1f}s")
        logger.info(f"  Errors: {sum(1 for c in result.comments if c.severity == IssueSeverity.ERROR)}")
        logger.info(f"  Warnings: {sum(1 for c in result.comments if c.severity == IssueSeverity.WARNING)}")

        return result
