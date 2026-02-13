# LangGraph Code Review Agent

A code review agent that reviews generated code against specifications using a structured workflow.

## StructuredCodeReviewAgent

**Workflow:**

| Phase | Description |
|-------|-------------|
| **0** | **Module extraction** – LLM extracts module name(s) from the spec; matches them to code directories (e.g. `vacancy_module`). If `module_names` is passed, this step is skipped. |
| **1** | **Discovery** – Find Python files under `code_root`, filtered by matched module(s). |
| **2** | **Validation** – Run validators in parallel: signatures, test quality, code quality, Pydantic usage, project structure, cross-file usage. |
| **2.5** | **Issue extraction** – Collect validator findings and filter by module. |
| **3** | **LLM analysis** – One LLM call to assign severity and suggestions to each issue. |
| **3a/3b** *(optional)* | **Pattern review** – If `pattern_guidelines_path` is set: a ReAct pattern-scout agent reads the codebase and reports design patterns; a second LLM call evaluates them against the guidelines and adds `design_pattern` issues. |
| **4** | **Report** – Build `ReviewResult` (passed, comments, summary, files_reviewed). |

## Features

- **Module-scoped review**: Infers target module from the spec and only reviews that module’s files.
- **Structured workflow**: Deterministic, fast, no tool loop.
- **Validators**: Signatures, test quality, code quality, Pydantic usage, project structure, cross-file usage (all run in parallel).
- **Optional pattern review**: With a pattern-guidelines folder (e.g. python-patterns-master), runs pattern identification and evaluation and adds design-pattern issues.
- **Configurable LLM**: Uses `GPT_OSS_*` (or equivalent) environment variables.
- **Pipeline integration**: `generate_cr_feedback_for_agent`, `CodeReviewRunner`, `quick_review`.

## Installation

The agent requires `langgraph` and `langchain-openai`. Add to your dependencies:

```bash
pip install langgraph langchain-openai langchain-core
```

For optional Langfuse tracing during local testing:

```bash
poetry add langfuse
```

Or add to `pyproject.toml`:

```toml
langgraph = "^0.2.0"
langchain-openai = "^0.2.0"
langchain-core = "^0.3.0"
```

## Configuration

Set the following environment variables:

```bash
export GPT_OSS_HOST="https://api.openai.com/v1"  # or your LLM API endpoint
export GPT_OSS_KEY="your-api-key"
export GPT_OSS_MODEL_NAME="gpt-4o"  # or your preferred model
```

### Optional: Langfuse tracing configuration

When these variables are set, traces for all LLM calls in the reviewer agent are sent to Langfuse:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_HOST="http://localhost:3000"

# Optional overrides
export REVIEW_AGENT_LANGFUSE_ENABLED="true"              # default auto-enables if keys are present
export LANGFUSE_TRACE_NAME="langgraph-code-review"       # default trace name
export LANGFUSE_SESSION_ID="review-local-M4"             # use this to group one run/session
```

## Run Langfuse locally (Docker Compose)

Use the official local self-host setup:

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
# Edit docker-compose.yml and replace every `CHANGEME` secret
docker compose up -d
```

Langfuse UI will be available at `http://localhost:3000` once containers are healthy.

## Usage

### Command line (example script)

The recommended way to run a review is the example script, which supports all options (context files, pattern guidelines, external components):

```bash
cd /path/to/OpenHands
poetry run python examples/code_review_agent/run_review.py

# With options (defaults point to test_data)
poetry run python examples/code_review_agent/run_review.py \
  --spec test_data/module_M4/M4.md \
  --code test_data/module_M4/M4_lvm_run \
  --output examples/code_review_agent/review_report.md \
  --data-structures test_data/api_data_structures.md \
  --guidelines test_data/guidelines/Agentic\ Coding\ Hints\ React\ Best\ Practices.md \
  --modules-description test_data/modules_description.md \
  --external-components test_data/AppFactory-components \
  --pattern-guidelines /path/to/python-patterns-master \
  --debug
```

With Langfuse explicitly enabled for this run:

```bash
poetry run python -m openhands.agenthub.langgraph_reviewer_agent.runner \
  --spec test_data/module_M4/M4.md \
  --code test_data/module_M4/M4_lvm_run \
  --module M4 \
  --enable-langfuse \
  --langfuse-session-id review-local-M4
```

- **`--spec`** – Specification file (required).
- **`--code`** – Generated code (directory or zip).
- **`--output`** – Report file (.md and .json).
- **`--data-structures`**, **`--guidelines`**, **`--modules-description`** – Optional context files.
- **`--external-components`** – Path to external package (e.g. AppFactory) for resolving imports; not validated.
- **`--pattern-guidelines`** – Path to pattern-guidelines folder (README.md + linked .md) to enable design-pattern review.
- **`--debug`** – Verbose logging and pattern-review LLM messages.

### Command line (module runner)

```bash
# Basic usage (module names inferred from spec)
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec /path/to/spec.md \
    --code /path/to/generated/code \
    --module ModuleName

# JSON output
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec /path/to/spec.md \
    --code /path/to/code \
    --module ModuleName \
    --output json

# Test tools only (no LLM)
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec /path/to/spec.md \
    --code /path/to/code \
    --module ModuleName \
    --test-tools
```

Note: `--stream` is accepted but the structured agent does not stream; it runs a full review and prints a note.

### Python API

```python
from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent

config = ReviewAgentConfig.from_env()
agent = StructuredCodeReviewAgent(config, verbose=True)

result = agent.review(
    spec_path="/path/to/spec.md",
    code_root="/path/to/code",
    module_names=None,       # auto-extract from spec; or e.g. ["vacancy_module"]
    data_structures=None,    # optional: API/model docs
    coding_guidelines=None,  # optional: coding rules
    modules_description=None,
    external_components_path=None,  # optional: path to external package for imports
    pattern_guidelines_path=None,   # optional: path to pattern-guidelines folder
)

print(result.to_markdown())
# result.passed, result.comments, result.summary, result.files_reviewed
# result.error_count, result.warning_count, result.info_count
```

### Pipeline Integration

```python
from openhands.agenthub.langgraph_reviewer_agent.integration import (
    CodeReviewRunner,
    generate_cr_feedback_for_agent,
    quick_review,
)

# Generate feedback for the coding agent
feedback = generate_cr_feedback_for_agent(
    spec_path="/path/to/spec.md",
    code_root="/path/to/code",
    module_name="MyModule",
)

# Pass feedback to coding agent
# coding_agent.add_message(feedback)

# Quick review (no LLM, just tools)
quick_result = quick_review(
    code_root="/path/to/code",
    module_name="MyModule",
)

# Stateful runner for multiple reviews
runner = CodeReviewRunner()
result1 = runner.run_review(spec_path1, code_root1, "Module1")
result2 = runner.run_review(spec_path2, code_root2, "Module2")

summary = runner.get_summary()
print(f"Total reviews: {summary['total_reviews']}")
print(f"Passed: {summary['passed']}")
```

### Context and options

- **`data_structures`** – API/Pydantic model docs; used to check implementation models and avoid duplicate definitions.
- **`coding_guidelines`** – Coding rules; the LLM uses them when assigning severity and suggestions.
- **`modules_description`** – High-level module architecture; used for scope checks.
- **`external_components_path`** – Path to an external package (e.g. AppFactory) so imports resolve; that code is not validated.
- **`module_names`** – If `None`, module names are inferred from the spec (Phase 0); otherwise only these directories are reviewed (e.g. `["vacancy_module"]`).
- **`pattern_guidelines_path`** – Path to a folder with `README.md` and linked `.md` pattern descriptions; enables Phase 3a/3b and `design_pattern` issues.

## Validators (used in Phase 2)

The structured agent runs these validators in parallel (per file where applicable):

- **`validate_signatures_tool`** – Method names, parameters, return types vs spec
- **`validate_test_quality_tool`** – Test coverage, superficial tests, mocking
- **`validate_code_quality_tool`** – Anti-patterns (bare except, exception suppression, mocks in prod)
- **`validate_pydantic_usage_tool`** – Pydantic usage and duplicate model definitions
- **`validate_project_structure_tool`** – File layout and misplaced files
- **`validate_cross_file_usage_tool`** – Cross-file references and invalid member access

Discovery uses `find_python_files_tool` and file reads. The **pattern scout** (Phase 3a, when pattern review is on) uses `find_python_files_tool`, `read_file_tool`, and `list_files_tool` to read the codebase, then `report_patterns_tool` for structured output.

## Issue categories

- `signature_mismatch` – Method signatures don’t match the spec
- `test_quality` – Test coverage or quality (e.g. superficial tests)
- `code_quality_issue` – Anti-patterns (bare except, exception suppression, mocks in prod)
- `pydantic_issue` – Pydantic usage / duplicate model definitions
- `structure_issue` – File organization / misplaced files
- `cross_file_issue` – Invalid cross-file or member access
- `design_pattern` – Pattern usage does not match the guideline (when pattern review is enabled)
- `guideline_violation`, `scope_violation`, `field_access_error`, `field_mapping_error`, `missing_implementation`, `type_error`, `data_structure_mismatch`, `general`

## Output format

`ReviewResult` has: `passed`, `comments`, `summary`, `files_reviewed`, and properties `error_count`, `warning_count`, `info_count`.

### Markdown

`result.to_markdown()` produces a report with status, summary, statistics, files reviewed, and issues grouped by category (each with severity, file, line, message, suggestion).

### JSON

`result.model_dump_json()` (or `model_dump()`) yields:

```json
{
  "passed": false,
  "comments": [
    {
      "category": "signature_mismatch",
      "severity": "error",
      "file_path": "src/mymodule/service.py",
      "line_number": 45,
      "message": "Method 'process_data' has incorrect parameter types",
      "suggestion": "Change parameter 'data' type from str to dict"
    }
  ],
  "summary": "Brief summary of the review.",
  "files_reviewed": ["src/mymodule/service.py", "tests/test_mymodule.py"]
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    StructuredCodeReviewAgent                     │
├─────────────────────────────────────────────────────────────────┤
│  Phase 0: MODULE EXTRACTION                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LLM: extract module name(s) from spec → match to dirs   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  Phase 1: DISCOVERY       ▼                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Read Spec  │  │ Find Files  │  │ Read Files  │ (by module)  │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│                           │                                      │
│  Phase 2: VALIDATION      ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Parallel: signatures, test quality, code quality,        │   │
│  │  pydantic, project structure, cross-file usage             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  Phase 2.5: EXTRACT ISSUES ▼ (filter by module)                  │
│                           │                                      │
│  Phase 3: LLM ANALYSIS   ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Single LLM: severity + suggestions per issue             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  Phase 3a/3b (optional)  ▼ if pattern_guidelines_path           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Pattern scout (ReAct) → identify patterns                │   │
│  │  LLM → evaluate vs guidelines → design_pattern issues     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│  Phase 4: REPORT         ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ReviewResult (passed, comments, summary, files_reviewed) │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Development

### Running tests

```bash
# Test tools without LLM
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec path/to/spec.md \
    --code path/to/generated/code \
    --module ModuleName \
    --test-tools

# Run the example review (uses test_data defaults)
poetry run python examples/code_review_agent/run_review.py --debug
```

### Adding a new validator

To add a new validator to the structured agent:

1. Implement the validator in `tools/validators.py` (or a new tool module) using `@tool` from `langchain_core.tools`.
2. In `structured_agent.py`, import it and add it to the list passed to `_run_validators` (and map its result in `_extract_issues_deterministically` if the output shape differs).
3. Optionally add a category in `models.IssueCategory` and map it in the category_map inside `_extract_issues_deterministically`.
