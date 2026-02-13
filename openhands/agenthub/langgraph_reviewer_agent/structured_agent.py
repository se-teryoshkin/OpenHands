"""Structured Workflow Code Review Agent.

This agent uses a deterministic workflow with parallel tool execution
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.models import (
    IdentifiedPattern,
    IssueCategory,
    IssueLocation,
    IssueSeverity,
    PatternIdentificationOutput,
    ReviewComment,
    ReviewResult,
)
from openhands.agenthub.langgraph_reviewer_agent.pattern_scout_agent import PatternScoutAgent
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
    issue_id: int | None = Field(default=None, description="Unique issue ID for reference in output")
    category: str = Field(description="Issue category: signature_mismatch, test_quality, code_quality_issue, pydantic_issue, structure_issue, missing_implementation, field_mapping_error, cross_file_issue, general")
    severity: str = Field(description="Severity: error, warning, info")
    locations: list[IssueLocation] = Field(default_factory=list, description="File/line locations for this issue")
    message: str = Field(description="Clear description of the issue")
    suggestion: str | None = Field(default=None, description="How to fix the issue")


class LLMReviewOutput(BaseModel):
    """Structured output from LLM analysis."""
    issues: list[ReviewIssue] = Field(default_factory=list, description="List of issues found")
    summary: str = Field(description="Brief summary of the review findings")
    passed: bool = Field(description="Whether the code passes review (no errors)")


class ExtractedIssue(BaseModel):
    """Issue extracted deterministically from validator output."""
    issue_id: int  # Unique ID for matching with LLM output
    category: str
    locations: list[IssueLocation] = Field(default_factory=list, description="File/line locations for this issue")
    message: str
    validator_name: str
    validator_issue: dict[str, Any]  # Original validator issue for context


class LLMIssueSeverity(BaseModel):
    """LLM output for a single issue - only severity and suggestion."""
    issue_id: int = Field(description="Issue ID to match with extracted issue")
    severity: str = Field(description="Severity: error, warning, info")
    suggestion: str | None = Field(default=None, description="How to fix the issue")


class LLMSeverityOutput(BaseModel):
    """Structured output from LLM - only severity and suggestions for issues."""
    issues: list[LLMIssueSeverity] = Field(description="List of severity assignments for issues")
    summary: str = Field(description="Brief summary of the review findings")
    passed: bool = Field(description="Whether the code passes review (no errors)")


class ModuleNames(BaseModel):
    """Module names extracted from specification."""
    reasoning: str = Field(description="Reason for module name selection with direct quote from specification")
    module_names: list[str] = Field(description="List of module names from code mentioned in the specification")
    confidence: str = Field(description="Confidence level: 'high', 'medium', or 'low'")


class MatchedModules(BaseModel):
    """Module directories matched with code structure."""
    reasoning: str = Field(description="Reasoning behind the match between module name in the specification with direct quote(s) and module names from code")
    matched_modules: list[str] = Field(description="List of module directory names that match the spec (e.g., ['screening_module', 'storage_module'])")
    unmatched_spec_modules: list[str] = Field(default_factory=list, description="Module names from spec that couldn't be matched")


class PatternEvaluationItem(BaseModel):
    """Evaluation of one identified pattern against the guideline."""
    pattern_name: str = Field(description="Name of the pattern (must match identification)")
    file_path: str = Field(description="File where the pattern was found")
    is_correct: bool = Field(description="True if the implementation matches the guideline; False otherwise")
    suggestion: str | None = Field(default=None, description="If not correct: proposed pattern or changes strictly according to the guideline; otherwise null")
    guideline_reference: str | None = Field(default=None, description="Relevant part of the guideline that was violated or followed")


class PatternEvaluationOutput(BaseModel):
    """Output from the pattern evaluation step."""
    evaluations: list[PatternEvaluationItem] = Field(default_factory=list, description="Evaluation for each identified pattern")


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
        self._langfuse_handler: Any | None = None
        self._langfuse_session_id: str | None = None

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
                top_p=self.config.top_p,
                api_key=api_key,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    def _invoke_structured_output(
        self,
        model: type[BaseModel],
        prompt: str,
        phase: str = "llm_call",
    ) -> BaseModel:
        """Invoke LLM and return validated Pydantic model output."""
        structured_llm = self.llm.with_structured_output(model)
        invoke_config = self._build_invoke_config(phase)
        response = structured_llm.invoke(prompt, config=invoke_config)

        if isinstance(response, dict):
            return model.model_validate(response.get("parsed", response))
        if isinstance(response, model):
            return response
        return model.model_validate(response)

    def _initialize_langfuse_handler(
        self,
        spec_path: str,
        code_root: str,
        module_names: list[str] | None,
    ) -> None:
        """Initialize optional Langfuse callback handler for this review run."""
        if not self.config.langfuse_enabled:
            self._langfuse_handler = None
            self._langfuse_session_id = None
            return

        try:
            try:
                from langfuse.langchain import CallbackHandler  # type: ignore
            except ImportError:
                from langfuse.callback import CallbackHandler  # type: ignore
        except ImportError:
            logger.warning(
                "Langfuse tracing requested, but package is not installed. "
                "Install with `poetry add langfuse`."
            )
            self._langfuse_handler = None
            self._langfuse_session_id = None
            return

        session_id = self.config.langfuse_session_id or (
            f"review-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        )
        user_id = os.getenv("USER") or os.getenv("USERNAME") or "local-reviewer"

        os.environ["LANGFUSE_PUBLIC_KEY"] = self.config.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = self.config.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = self.config.langfuse_host
        os.environ["LANGFUSE_TRACE_NAME"] = self.config.langfuse_trace_name
        os.environ["LANGFUSE_SESSION_ID"] = session_id
        os.environ["LANGFUSE_USER_ID"] = user_id

        self._langfuse_handler = CallbackHandler()
        self._langfuse_session_id = session_id
        logger.info(
            "Langfuse tracing enabled: host=%s trace_name=%s session_id=%s spec=%s code_root=%s modules=%s",
            self.config.langfuse_host,
            self.config.langfuse_trace_name,
            session_id,
            spec_path,
            code_root,
            module_names or "auto",
        )

    def _build_invoke_config(self, phase: str) -> RunnableConfig:
        """Build invocation config with optional Langfuse callback."""
        config: RunnableConfig = {"metadata": {"review_phase": phase}}
        if self._langfuse_handler is not None:
            config["callbacks"] = [self._langfuse_handler]
            config["tags"] = ["reviewer-agent", phase]
        return config

    def _flush_langfuse(self) -> None:
        """Flush Langfuse events if handler supports it."""
        if self._langfuse_handler is None:
            return
        try:
            flush_method = getattr(self._langfuse_handler, "flush", None)
            if callable(flush_method):
                flush_method()
        except Exception as e:
            logger.debug(f"Failed to flush Langfuse callback handler: {e}")

    def _extract_module_names_from_spec(self, spec_content: str) -> list[str]:
        """Extract module names from specification using LLM structured output."""
        prompt = f"""Analyze the following specification and extract the names of modules that this specification is directed towards to.

Look for:
- Module names mentioned in headers that are defined to be implemented by this specification

Pay attention that there can be also some mentions of existing modules that we are not interested in.

Return ONLY the module names (e.g., "модуль хранения", "storage module"), not the full descriptions.

Specification:
{spec_content[:4000]}  # Truncate as for now we suppose that module name is somewhere in the beginning

If no modules are clearly identified, return an empty list with "low" confidence."""
        try:
            module_data = self._invoke_structured_output(
                ModuleNames,
                prompt,
                phase="module_extraction",
            )
        except Exception as e:
            logger.warning(f"Failed to extract module names from spec: {e}")
            return []

        assert isinstance(module_data, ModuleNames)
        logger.info(f"Extracted modules from specification {module_data.module_names} with reasoning: {module_data.reasoning}")

        self._log(
            f"Extracted module names from spec: {module_data.module_names} "
            f"(confidence: {module_data.confidence})"
        )
        return module_data.module_names

    def _match_modules_with_code_structure(
        self,
        spec_module_names: list[str],
        code_root: str
    ) -> list[str]:
        """Match module names from spec with actual directory structure using LLM.

        Args:
            spec_module_names: Module names extracted from spec
            code_root: Root directory of the code

        Returns:
            List of matched module directory names (e.g., ['screening_module'])
        """
        if not spec_module_names:
            return []

        # Discover available module directories
        root = Path(code_root)
        available_modules = set()

        # Look for src/ directories
        src_dir = root / "src"
        if src_dir.exists():
            for item in src_dir.iterdir():
                if item.is_dir():  # and item.name.endswith("_module"):
                    available_modules.add(item.name)

        # Look for tests/ directories
        tests_dir = root / "tests"
        if tests_dir.exists():
            for item in tests_dir.iterdir():
                if item.is_dir(): # and item.name.endswith("_module"):
                    available_modules.add(item.name)

        # self._log(f"Available modules found: {available_modules}")
        logger.info(f"Available modules found: {available_modules}")

        if not available_modules:
            self._log("No module directories found in code structure")
            return []

        # Use LLM to match spec module names with actual directories
        prompt = f"""Match the module names from the specification with the actual module directories found in the code structure.

Specification module names (what the spec mentions):
{json.dumps(spec_module_names, indent=2)}

Available module directories in code:
{json.dumps(sorted(available_modules), indent=2)}

Your task:
1. Match each specification module name to the corresponding directory name
2. Consider variations (e.g., "screening" → "screening_module", "Модуль скрининга" → "screening_module")
3. Consider partial matches (e.g., "screening" → "screening_module")
4. Return ONLY the matched directory names that exist in the code

If a spec module name clearly matches a directory (even with variations), include it in matched_modules."""
        try:
            matched_data = self._invoke_structured_output(
                MatchedModules,
                prompt,
                phase="module_matching",
            )
        except Exception as e:
            logger.warning(f"Failed to match modules with code structure: {e}")
            matched_data = None

        assert isinstance(matched_data, MatchedModules)

        matched_modules = matched_data.matched_modules
        logger.info(f"Matched modules: {matched_data.matched_modules} with reasoning: {matched_data.reasoning}")

        if not matched_modules:
            for spec_name in spec_module_names:
                normalized = spec_name.lower().replace(" ", "_")
                if not normalized.endswith("_module"):
                    normalized = f"{normalized}_module"
                if normalized in available_modules:
                    matched_modules.append(normalized)

        # Validate that matched modules actually exist
        validated_modules = [
            m for m in matched_modules
            if m in available_modules
        ]

        if validated_modules != matched_modules:
            logger.warning(
                f"LLM matched modules {matched_modules} but only {validated_modules} exist in code"
            )

        self._log(f"Matched modules: {validated_modules}")
        return validated_modules

    def _discover_files(self, code_root: str, module_names: list[str] | None = None) -> dict:
        """Discover Python files in the code root, optionally filtered by module names.

        Args:
            code_root: Root directory to search
            module_names: Optional list of module directory names to filter by
                          (e.g., ['screening_module']). If None, discovers all files.
        """
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

            relative_path = str(py_file.relative_to(root))

            # Filter by module names if provided
            if module_names:
                # Check if file belongs to any of the specified modules
                belongs_to_module = False
                for module_name in module_names:
                    # Match src/{module_name}/ or tests/{module_name}/
                    if f"src/{module_name}/" in relative_path or f"tests/{module_name}/" in relative_path:
                        belongs_to_module = True
                        break

                if not belongs_to_module:
                    continue

            file_info = {
                "path": str(py_file),
                "name": py_file.name,
                "relative": relative_path,
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

    def _load_pattern_guidelines(self, pattern_guidelines_path: str | Path) -> str:
        """Load pattern guidelines from a folder (e.g. python-patterns-master).

        Reads README.md and all linked .md files referenced in it (relative links).
        Returns concatenated text for use in pattern evaluation.
        """
        root = Path(pattern_guidelines_path).resolve()
        if not root.is_dir():
            return f"Error: not a directory: {root}"

        readme_path = root / "README.md"
        if not readme_path.exists():
            readme_path = root / "readme.md"
        if not readme_path.exists():
            return f"Error: README.md not found in {root}"

        try:
            readme_content = readme_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading README: {e}"

        # Find all markdown links: [text](path) or [text](path.md)
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)\)')
        seen: set[Path] = {readme_path.resolve()}
        parts = [f"# Pattern guidelines index (README)\n\n{readme_content}"]
        total_chars = len(parts[0])
        max_total_chars = 80_000  # Cap to avoid token overflow

        for _label, rel_path in link_pattern.findall(readme_content):
            rel_path = rel_path.strip()
            if not rel_path or rel_path.startswith("http"):
                continue
            file_path = (root / rel_path).resolve()
            if not file_path.is_file() or file_path in seen:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception:
                continue
            seen.add(file_path)
            part = f"\n\n# Pattern: {file_path.name}\n\n{text}"
            if total_chars + len(part) > max_total_chars:
                part = part[: max_total_chars - total_chars] + "\n\n[... truncated]"
            parts.append(part)
            total_chars += len(part)
            if total_chars >= max_total_chars:
                break

        return "".join(parts)

    def _run_single_validator(self, name: str, func: Callable, args: dict) -> tuple[str, Any]:
        """Run a single validator and return (name, result)."""
        try:
            result = func(args)
            if isinstance(result, str):
                return (name, json.loads(result))
            return (name, result)
        except Exception as e:
            logger.warning(f"Validator {name} failed: {e}")
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

        # 2. Code quality validation tasks (for each source file and each test file)
        for file_info in files.get("source_files", []):
            tasks.append((
                f"code_quality_{file_info['name']}",
                validate_code_quality_tool.invoke,
                {"file_path": file_info["path"]}
            ))
        for file_info in files.get("test_files", []):
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
        extracted_issues: list[ExtractedIssue],
        coding_guidelines: str = "",
        modules_description: str = "",
    ) -> str:
        """Build the prompt for LLM analysis."""
        # Prepare extracted issues list for LLM (minimal - ID, category, location(s), message)
        issues_list = []
        for issue in extracted_issues:
            if issue.locations:
                loc_parts = [f"{loc.file_path}:{loc.line_number}" if loc.line_number is not None else loc.file_path for loc in issue.locations]
                loc_str = " | ".join(loc_parts)
            else:
                loc_str = "unknown"
            issues_list.append(
                f"ID {issue.issue_id}: [{issue.category}] {loc_str} - {issue.message[:200]}"
            )

        prompt = f"""You are a code review expert. Your task is to assign severity levels and generate suggestions for the issues found by validators.

## Specification
```
{spec_content[:4000]}
```

## Issues Found by Validators

The following issues were found deterministically. You MUST process ALL of them by their ID:

{chr(10).join(issues_list)}

"""

        if coding_guidelines:
            prompt += f"""
## Coding Guidelines
{coding_guidelines}
"""

        if modules_description:
            prompt += f"""
## Module Architecture
{modules_description[:1500]}
"""

        prompt += """
## Your Task

**IMPORTANT: Process ALL issues listed above by their ID.**

For EACH issue ID, you need to:
1. **Assign severity**: error (must fix), warning (should fix), info (suggestion)
2. **Generate suggestion**: A concrete, self-contained suggestion for how to fix this specific issue.

**Suggestion rules:**
- Write the full fix in the suggestion; do NOT reference other issues (e.g. do NOT say "Same as ID 13" or "See issue 5").
- Each suggestion must stand alone so the developer can act on it without looking up another issue.

**Severity Assignment Rules:**
- **error**: Critical issues that must be fixed
  - Missing required methods (signature_mismatch)
  - Code that will cause runtime failures
  - Mocks instead of working code
  - Code with try ... except Exception
  - Large blocks of code under try ... except
  - Critical security or correctness issues
- **warning**: Issues that should be addressed
  - Code quality issues
  - Test quality issues (superficial tests)
  - Potential bugs or suboptimal patterns
- **info**: Suggestions for improvement
  - Style improvements
  - Documentation suggestions
  - Minor optimizations

**CRITICAL RULES:**
- Process ALL issue IDs listed above (same number of issues in output as input)
- Use the exact issue_id to match your response
- Only provide severity and suggestion
- Do NOT skip any issue IDs

**Output Format:**
Respond with JSON:
{
  "issues": [
    {
      "issue_id": <integer ID from input>,
      "severity": "error|warning|info",
      "suggestion": "<concrete, self-contained fix for this issue only; do not reference other issue IDs>"
    },
    ...
  ],
  "summary": "Brief summary (1-2 sentences)",
  "passed": true/false (false only if ERROR severity issues exist)
}
"""

        return prompt

    def _build_pattern_identification_prompt(
        self,
        spec_content: str,
        file_contents: dict[str, str],
    ) -> str:
        """Build prompt for step 1: identify all design patterns in the code."""
        code_blobs = []
        for path, content in list(file_contents.items())[:30]:  # Limit files
            short_path = Path(path).name
            code_blobs.append(f"### File: {path}\n```python\n{content[:4000]}\n```")
        code_section = "\n\n".join(code_blobs)

        return f"""You are a senior developer analyzing code for design patterns.

## Specification (context)
```
{spec_content[:2500]}
```

## Code to analyze
{code_section}

## Your task
1. Look through the code (and infer from connected modules if needed) and identify all design patterns used.
2. For each pattern found, report:
   - pattern_name: canonical name of the pattern (e.g., Singleton, Factory, Adapter)
   - file_path: full path to the file
   - line_numbers: list of relevant line numbers where the pattern is evident
   - class_or_function_names: names of classes or functions that implement or use this pattern
   - rationale: brief explanation of why this pattern is used here

Include only patterns that are clearly present (e.g., a single shared instance, factory function, protocol/interface). Do not list generic OOP unless it is a named pattern.
If no clear design patterns are found, return an empty list.
"""

    def _build_pattern_evaluation_prompt(
        self,
        identified_patterns: list[IdentifiedPattern],
        pattern_guidelines: str,
    ) -> str:
        """Build prompt for step 2: evaluate each pattern against the guideline."""
        patterns_text = "\n\n".join(
            f"- Pattern: {p.pattern_name}\n  File: {p.file_path}\n  Lines: {p.line_numbers}\n  Classes/functions: {p.class_or_function_names}\n  Rationale: {p.rationale}"
            for p in identified_patterns
        )
        return f"""You are a code reviewer. Evaluate each identified pattern against the official pattern guidelines below.

## Identified patterns in the code
{patterns_text}

## Pattern guidelines (README + linked pattern descriptions)
{pattern_guidelines[:60000]}

## Your task
For EACH identified pattern:
1. Look up the corresponding pattern in the guidelines (by name or close match).
2. Decide: does the code's use of this pattern match the guideline? (is_correct: true/false)
3. If not correct: provide a suggestion strictly based on the guideline (proposed pattern or concrete changes). Set guideline_reference to the relevant guideline excerpt.
4. If correct: set suggestion and guideline_reference to null.

Output one evaluation per identified pattern, in the same order. Be strict: only set is_correct=true when the implementation aligns with the guideline.
"""

    def _run_pattern_identification(
        self,
        spec_content: str,
        file_contents: dict[str, str],
    ) -> PatternIdentificationOutput:
        """Step 1: LLM identifies all design patterns in the code."""
        prompt = self._build_pattern_identification_prompt(spec_content, file_contents)
        try:
            out = self._invoke_structured_output(
                PatternIdentificationOutput,
                prompt,
                phase="pattern_identification",
            )
            assert isinstance(out, PatternIdentificationOutput)
            return out
        except Exception as e:
            logger.warning(f"Pattern identification failed: {e}")
            return PatternIdentificationOutput(patterns=[])

    def _run_pattern_evaluation(
        self,
        identified_patterns: list[IdentifiedPattern],
        pattern_guidelines: str,
    ) -> PatternEvaluationOutput:
        """Step 2: LLM evaluates each pattern against the guidelines."""
        if not identified_patterns:
            return PatternEvaluationOutput(evaluations=[])
        prompt = self._build_pattern_evaluation_prompt(identified_patterns, pattern_guidelines)
        try:
            out = self._invoke_structured_output(
                PatternEvaluationOutput,
                prompt,
                phase="pattern_evaluation",
            )
            assert isinstance(out, PatternEvaluationOutput)
            return out
        except Exception as e:
            logger.warning(f"Pattern evaluation failed: {e}")
            return PatternEvaluationOutput(evaluations=[])

    def _pattern_evaluations_to_issues(
        self,
        identified_patterns: list[IdentifiedPattern],
        evaluation_output: PatternEvaluationOutput,
        start_issue_id: int | None = None,
    ) -> list[ReviewIssue]:
        """Convert pattern evaluations (where is_correct=False) to ReviewIssue list."""
        evals_by_name_file: dict[tuple[str, str], PatternEvaluationItem] = {}
        for ev in evaluation_output.evaluations:
            evals_by_name_file[(ev.pattern_name, ev.file_path)] = ev

        issues: list[ReviewIssue] = []
        for idx, pat in enumerate(identified_patterns):
            ev = evals_by_name_file.get((pat.pattern_name, pat.file_path))
            if ev is None or ev.is_correct:
                continue
            locations = (
                [IssueLocation(file_path=pat.file_path, line_number=ln) for ln in pat.line_numbers]
                if pat.line_numbers
                else [IssueLocation(file_path=pat.file_path, line_number=None)]
            )
            message = (
                f"Pattern '{pat.pattern_name}' usage does not match the guideline. "
                f"Classes/functions: {', '.join(pat.class_or_function_names)}. "
                + (f"Guideline: {ev.guideline_reference}" if ev.guideline_reference else "")
            )
            issue_id = (start_issue_id + idx) if start_issue_id is not None else None
            issues.append(ReviewIssue(
                issue_id=issue_id,
                category="design_pattern",
                severity="warning",
                locations=locations,
                message=message.strip(),
                suggestion=ev.suggestion,
            ))
        return issues

    def _extract_issues_deterministically(
        self,
        validation_results: dict[str, Any],
        module_names: list[str] | None = None,
    ) -> list[ExtractedIssue]:
        """Extract all issues from validators deterministically.

        This phase extracts issues without LLM involvement, ensuring 100% consistency.
        Returns list of ExtractedIssue objects with category, locations, message.

        Args:
            validation_results: Results from all validators
            module_names: Optional list of module names to filter issues by
        """
        extracted_issues = []

        # Category mapping rules (deterministic)
        category_map = {
            "cross_file_usage": "cross_file_issue",
            "signatures_": "signature_mismatch",
            "test_quality_": "test_quality",
            "code_quality_": "code_quality_issue",
            "pydantic_usage": "pydantic_issue",
            "pydantic_": "pydantic_issue",
            "project_structure": "structure_issue",
        }

        for validator_name, result in validation_results.items():
            if not isinstance(result, dict):
                continue

            # Determine category from validator name
            category = "general"
            for pattern, mapped_category in category_map.items():
                if validator_name.startswith(pattern) or validator_name == pattern:
                    category = mapped_category
                    break

            # Get file path from result level (for validators that store it there)
            result_file = result.get("file", "")

            # Extract all issues (no truncation!)
            issues = result.get("issues", [])
            for issue in issues:
                # Extract file_path
                file_path = issue.get("file") or result_file
                if not file_path:
                    # Try to infer from validator name (e.g., "signatures_service.py" -> extract service.py)
                    # Check file_path_mapping for validators that use it
                    if validator_name.startswith("signatures_") or \
                       validator_name.startswith("code_quality_") or \
                       validator_name.startswith("test_quality_"):
                        # Check file_path_mapping
                        if hasattr(self, '_file_path_mapping') and validator_name in self._file_path_mapping:
                            file_path = self._file_path_mapping[validator_name]
                        else:
                            file_path = "unknown"
                    else:
                        file_path = "unknown"

                # Use issue["locations"] when validator provides it (e.g. duplicate_model: list of {file, line})
                locations: list[IssueLocation] = []
                raw_locs = issue.get("locations")
                if raw_locs and isinstance(raw_locs, list) and len(raw_locs) > 0:
                    loc0 = raw_locs[0]
                    if isinstance(loc0, dict):
                        locations = [
                            IssueLocation(
                                file_path=(loc.get("file") or loc.get("file_path") or "").strip() or "unknown",
                                line_number=loc.get("line") if loc.get("line") is not None else loc.get("line_number"),
                            )
                            for loc in raw_locs
                        ]
                    else:
                        raw_locs = None  # fall through to normal extraction
                else:
                    raw_locs = None

                if raw_locs is None:
                    # Extract line number(s): single "line" or list "lines" (same file); optional "files" for multiple files
                    line_number = issue.get("line")
                    lines_list = issue.get("lines")
                    if lines_list is not None and isinstance(lines_list, list):
                        line_numbers = [ln for ln in lines_list if ln is not None]
                    else:
                        line_numbers = [line_number] if line_number is not None else []

                    extra_files = issue.get("files")
                    if extra_files is not None and not isinstance(extra_files, list):
                        extra_files = [extra_files]
                    if not extra_files:
                        extra_files = []

                    if line_numbers:
                        if extra_files and len(extra_files) == len(line_numbers):
                            locations = [
                                IssueLocation(
                                    file_path=(extra_files[i] or "").strip() or "unknown",
                                    line_number=line_numbers[i],
                                )
                                for i in range(len(line_numbers))
                            ]
                        else:
                            locations = [
                                IssueLocation(
                                    file_path=file_path or "unknown",
                                    line_number=ln,
                                )
                                for ln in line_numbers
                            ]
                    else:
                        locations = [
                            IssueLocation(
                                file_path=file_path or "unknown",
                                line_number=line_number,
                            )
                        ]

                # Filter by module names if provided (use first location's file).
                # Only exclude when path clearly lies under src/<other>/ or tests/<other>/ with other not in module_names.
                # Include paths that don't use src/ or tests/ (e.g. top-level or flat layout) so cross-file issues aren't dropped.
                primary_file = locations[0].file_path if locations else "unknown"
                if module_names and primary_file and primary_file != "unknown":
                    primary_norm = primary_file.replace("\\", "/") if isinstance(primary_file, str) else primary_file
                    belongs_to_module = False
                    # Paths under src/<name>/ or tests/<name>/: only keep if name is in module_names
                    if "src/" in primary_norm or "tests/" in primary_norm:
                        for module_name in module_names:
                            if f"src/{module_name}/" in primary_norm or f"tests/{module_name}/" in primary_norm:
                                belongs_to_module = True
                                break
                        if not belongs_to_module:
                            continue
                    # Else: path has no src/ or tests/ (e.g. flat layout); don't filter out, include the issue

                # Extract message
                message = issue.get("message", str(issue))

                # Create extracted issue with ID
                issue_id = len(extracted_issues) + 1
                extracted_issues.append(ExtractedIssue(
                    issue_id=issue_id,
                    category=category,
                    locations=locations,
                    message=message,
                    validator_name=validator_name,
                    validator_issue=issue,
                ))

        return extracted_issues

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

    def _merge_llm_output_with_extracted_issues(
        self,
        llm_output: LLMSeverityOutput,
        extracted_issues: list[ExtractedIssue],
    ) -> LLMReviewOutput:
        """Merge LLM output with deterministically extracted issues.

        The LLM only provides severity and suggestions by issue_id. We merge these with
        the pre-extracted issues to ensure all issues are included.
        """
        # Create a mapping from issue_id to LLM output
        issue_map: dict[int, LLMIssueSeverity] = {}
        for llm_issue in llm_output.issues:
            issue_map[llm_issue.issue_id] = llm_issue

        # Merge extracted issues with LLM output by ID
        merged_issues: list[ReviewIssue] = []
        for extracted in extracted_issues:
            llm_issue = issue_map.get(extracted.issue_id)

            if llm_issue:
                # Use LLM's severity and suggestion
                merged_issues.append(ReviewIssue(
                    issue_id=extracted.issue_id,
                    category=extracted.category,
                    severity=llm_issue.severity,
                    locations=extracted.locations,
                    message=extracted.message,
                    suggestion=llm_issue.suggestion,
                ))
            else:
                # LLM didn't provide output for this issue, use defaults
                primary = extracted.locations[0].file_path if extracted.locations else "unknown"
                logger.warning(
                    f"LLM didn't provide severity/suggestion for issue ID {extracted.issue_id}: "
                    f"{extracted.category} in {primary}"
                )
                merged_issues.append(ReviewIssue(
                    issue_id=extracted.issue_id,
                    category=extracted.category,
                    severity="warning",  # Default severity
                    locations=extracted.locations,
                    message=extracted.message,
                    suggestion=None,
                ))

        # Ensure we have all extracted issues (defense in depth)
        if len(merged_issues) != len(extracted_issues):
            logger.warning(
                f"Issue count mismatch: extracted={len(extracted_issues)}, "
                f"merged={len(merged_issues)}. Using extracted issues."
            )
            merged_issues = [
                ReviewIssue(
                    issue_id=extracted.issue_id,
                    category=extracted.category,
                    severity="warning",
                    locations=extracted.locations,
                    message=extracted.message,
                    suggestion=None,
                )
                for extracted in extracted_issues
            ]

        return LLMReviewOutput(
            issues=merged_issues,
            summary=llm_output.summary,
            passed=llm_output.passed,
        )

    def _create_fallback_output(
        self,
        extracted_issues: list[ExtractedIssue],
        error_msg: str = "",
    ) -> LLMReviewOutput:
        """Create fallback output when LLM fails, using extracted issues with default severity."""
        issues = [
            ReviewIssue(
                issue_id=extracted.issue_id,
                category=extracted.category,
                severity="warning",  # Default severity
                locations=extracted.locations,
                message=extracted.message,
                suggestion=None,
            )
            for extracted in extracted_issues
        ]

        summary = f"Code review completed. {len(issues)} issues found."
        if error_msg:
            summary += f" (LLM analysis failed: {error_msg})"

        # Determine passed status based on issue categories
        has_errors = any(
            issue.category == "signature_mismatch" or
            issue.category == "cross_file_issue"
            for issue in issues
        )

        return LLMReviewOutput(
            issues=issues,
            summary=summary,
            passed=not has_errors,
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

            normalized_locations = [
                IssueLocation(
                    file_path=self._normalize_file_path(loc.file_path),
                    line_number=loc.line_number,
                )
                for loc in (issue.locations or [])
            ]
            if not normalized_locations:
                normalized_locations = [IssueLocation(file_path="unknown", line_number=None)]

            comments.append(ReviewComment(
                category=category,
                severity=severity,
                locations=normalized_locations,
                message=issue.message,
                suggestion=issue.suggestion,
                issue_id=issue.issue_id,
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
        data_structures: str | None = None,
        coding_guidelines: str | None = None,
        modules_description: str | None = None,
        external_components_path: str | None = None,
        module_names: list[str] | None = None,
        pattern_guidelines_path: str | Path | None = None,
    ) -> ReviewResult:
        """Run a structured code review.

        Workflow:
        1. Discovery: Read spec and discover files (optionally filtered by module names)
        2. Validation: Run all validators in parallel (signatures, code quality,
           test quality, Pydantic usage, project structure)
        3. Analysis: Single LLM call to interpret results
        3a. Pattern identification (if pattern_guidelines_path): LLM finds patterns in code
        3b. Pattern evaluation (if pattern_guidelines_path): LLM checks each against guidelines
        4. Report: Generate structured output

        Args:
            spec_path: Path to specification file
            code_root: Root directory of code to review
            data_structures: Optional API data structures
            coding_guidelines: Optional coding guidelines
            modules_description: Optional modules description
            external_components_path: Optional path to external components
            module_names: Optional list of module directory names to filter by
                         (e.g., ['screening_module']). If None, will extract from spec.
            pattern_guidelines_path: Optional path to pattern guidelines folder (e.g. python-patterns-master)
                         containing README.md and linked pattern descriptions. If set, runs pattern
                         identification and evaluation and adds design_pattern issues to the report.
        """
        self._start_time = datetime.now()
        self._code_root = Path(code_root).resolve()  # Store for path normalization
        logger.info("Starting structured code review")
        logger.info(f"  Spec: {spec_path}")
        logger.info(f"  Code: {code_root}")
        self._initialize_langfuse_handler(spec_path, code_root, module_names)

        # Phase 0: Extract and match module names from spec
        phase_start = datetime.now()
        self._log("Phase 0: Module Name Extraction")
        spec_content = self._read_file(spec_path)

        if module_names is None:
            # Extract module names from spec using LLM
            spec_module_names = self._extract_module_names_from_spec(spec_content)
            # Match with actual code structure
            module_names = self._match_modules_with_code_structure(spec_module_names, code_root)
            if module_names:
                self._log(f"  Filtering review to modules: {module_names}")
            else:
                self._log("  No modules matched, reviewing all files")
        else:
            self._log(f"  Using provided module names: {module_names}")

        module_extraction_time = (datetime.now() - phase_start).total_seconds()
        self._log(f"  Module extraction completed in {module_extraction_time:.2f}s")

        # Phase 1: Discovery
        phase_start = datetime.now()
        self._log("Phase 1: Discovery")
        files = self._discover_files(code_root, module_names=module_names)
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
            self._file_path_mapping[f"code_quality_{file_name}"] = file_path

        # Count issues from validators and log per-validator (always at INFO so user can see why cross-file etc. is empty)
        total_validator_issues = 0
        for name, result in validation_results.items():
            if not isinstance(result, dict):
                logger.info(f"  Validator {name}: no result dict")
                continue
            if "error" in result:
                logger.info(f"  Validator {name}: error - {result['error'][:120]}")
                continue
            issues_in_result = result.get("issues", [])
            count = len(issues_in_result)
            total_validator_issues += count
            logger.info(f"  Validator {name}: {count} issues")
        self._log(f"  Validators found {total_validator_issues} potential issues")

        # Phase 2.5: Deterministic Issue Extraction
        phase_start = datetime.now()
        self._log("Phase 2.5: Deterministic Issue Extraction")
        extracted_issues = self._extract_issues_deterministically(
            validation_results,
            module_names=module_names
        )
        extraction_time = (datetime.now() - phase_start).total_seconds()
        self._log(f"  Extracted {len(extracted_issues)} issues deterministically in {extraction_time:.2f}s")

        # Phase 3: LLM Analysis (Severity Assignment & Suggestions Only)
        self._log("Phase 3: LLM Analysis (Severity & Suggestions)")
        prompt = self._build_analysis_prompt(
            spec_content=spec_content,
            file_contents=file_contents,
            extracted_issues=extracted_issues,
            coding_guidelines=coding_guidelines or "",
            modules_description=modules_description or "",
        )

        try:
            llm_structured = self._invoke_structured_output(
                LLMSeverityOutput,
                prompt,
                phase="issue_severity_assignment",
            )
            assert isinstance(llm_structured, LLMSeverityOutput)
            llm_output = self._merge_llm_output_with_extracted_issues(
                llm_structured,
                extracted_issues,
            )
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            llm_output = self._create_fallback_output(extracted_issues, str(e))

        # Phase 3a/3b: Design pattern identification and evaluation (optional)
        if not pattern_guidelines_path:
            if self.verbose:
                logger.info("Pattern review skipped (no --pattern-guidelines path). Use --pattern-guidelines <path> to enable.")
        else:
            self._log("Phase 3a: Pattern Identification (ReAct pattern scout)")
            pattern_guidelines_text = self._load_pattern_guidelines(pattern_guidelines_path)
            if pattern_guidelines_text.startswith("Error"):
                logger.warning(f"Pattern guidelines not loaded: {pattern_guidelines_text[:200]}")
            else:
                scout = PatternScoutAgent(
                    config=self.config,
                    max_steps=50,
                    verbose=self.verbose,
                )
                if self._langfuse_handler is not None:
                    scout.callbacks = [self._langfuse_handler]
                identification = scout.run(spec_path=spec_path, code_root=code_root)
                if self.verbose and identification.patterns:
                    for p in identification.patterns:
                        logger.info(
                            "[pattern scout result] %s @ %s: %s",
                            p.pattern_name,
                            p.file_path,
                            (p.rationale[:200] + "…") if p.rationale and len(p.rationale) > 200 else (p.rationale or ""),
                        )
                if identification.patterns:
                    self._log(f"  Identified {len(identification.patterns)} pattern(s) in code")
                    self._log("Phase 3b: Pattern Evaluation")
                    evaluation = self._run_pattern_evaluation(
                        identification.patterns,
                        pattern_guidelines_text,
                    )
                    if self.verbose:
                        for ev in evaluation.evaluations:
                            logger.info(
                                "[pattern evaluation LLM] %s @ %s: is_correct=%s suggestion=%s",
                                ev.pattern_name,
                                ev.file_path,
                                ev.is_correct,
                                (ev.suggestion or "")[:200],
                            )
                    next_id = (
                        max((i.issue_id for i in llm_output.issues if i.issue_id is not None), default=0) + 1
                    )
                    pattern_issues = self._pattern_evaluations_to_issues(
                        identification.patterns,
                        evaluation,
                        start_issue_id=next_id,
                    )
                    if pattern_issues:
                        llm_output.issues.extend(pattern_issues)
                        self._log(f"  Added {len(pattern_issues)} pattern guideline issue(s)")

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
        self._flush_langfuse()

        return result
