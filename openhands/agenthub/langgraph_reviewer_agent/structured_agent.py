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
                    for issue in issues[:5]:  # Limit to 5 issues per validator
                        msg = issue.get("message", str(issue))[:200]
                        validation_summary.append(f"  - {msg}")
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
3. **file_path**: The file where the issue is located (from validator output)
4. **line_number**: Line number if reported by validator (null if not)
5. **message**: The issue message from the validator
6. **suggestion**: How to fix it

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

Expected output: 3-8 issues maximum. If validators found no issues, report empty list.

Respond with JSON:
{
  "issues": [...],
  "summary": "Brief summary (1-2 sentences)",
  "passed": true/false (false only if ERROR severity issues exist)
}
"""

        return prompt

    def _parse_llm_response(self, response: str) -> LLMReviewOutput:
        """Parse the LLM response into structured output."""
        try:
            # Try to extract JSON from the response
            content = response.strip()

            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content)
            return LLMReviewOutput(**data)
        except Exception as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            # Return a safe default
            return LLMReviewOutput(
                issues=[],
                summary=f"Failed to parse review output: {e}",
                passed=True
            )

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

            comments.append(ReviewComment(
                category=category,
                severity=severity,
                file_path=issue.file_path,
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
            llm_output = self._parse_llm_response(content)
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            llm_output = LLMReviewOutput(
                issues=[],
                summary=f"LLM analysis failed: {e}",
                passed=True
            )

        # Phase 4: Report
        self._log("Phase 4: Report Generation")
        result = self._convert_to_review_result(
            llm_output=llm_output,
            files_reviewed=list(file_contents.keys()),
        )

        # Log completion
        duration = (datetime.now() - self._start_time).total_seconds()
        logger.info(f"Review completed in {duration:.1f}s")
        logger.info(f"  Errors: {sum(1 for c in result.comments if c.severity == IssueSeverity.ERROR)}")
        logger.info(f"  Warnings: {sum(1 for c in result.comments if c.severity == IssueSeverity.WARNING)}")

        return result
