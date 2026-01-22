# LangGraph Code Review Agent

A ReAct-based code review agent built with LangGraph that automatically reviews generated code against specifications.

## Features

- **ReAct Pattern**: Uses LangGraph's `create_react_agent` for structured reasoning and action
- **Specification-based Review**: Validates code against provided specifications
- **Multiple Validation Types**:
  - Signature validation (method names, parameters, return types)
  - Field access validation (detecting access to non-existent fields)
  - Data mapping validation (ensuring complete model mappings)
  - Test quality analysis (coverage, mocking detection)
  - Project structure validation
- **Configurable LLM**: Uses separate environment variables for the review model
- **Streaming Support**: Watch the agent work in real-time
- **Pipeline Integration**: Easy to integrate with the main coding agent

## Installation

The agent requires `langgraph` and `langchain-openai`. Add to your dependencies:

```bash
pip install langgraph langchain-openai langchain-core
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

## Usage

### Command Line

```bash
# Basic usage
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec /path/to/spec.md \
    --code /path/to/generated/code \
    --module ModuleName

# With streaming output
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec /path/to/spec.md \
    --code /path/to/code \
    --module ModuleName \
    --stream --verbose

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

### Python API

```python
from openhands.agenthub.langgraph_reviewer_agent import (
    CodeReviewAgent,
    ReviewAgentConfig,
    run_review,
)

# Simple usage
result = run_review(
    spec_path="/path/to/spec.md",
    code_root="/path/to/code",
    module_name="MyModule",
)

print(result.to_markdown())

# With custom configuration
config = ReviewAgentConfig(
    llm_model_name="gpt-4o-mini",
    temperature=0.0,
    strict_mode=True,
)

agent = CodeReviewAgent(config)
result = agent.review(
    spec_path="/path/to/spec.md",
    code_root="/path/to/code",
    module_name="MyModule",
)

# Check results
if result.passed:
    print("Review passed!")
else:
    print(f"Found {result.error_count} errors")
    for comment in result.comments:
        print(f"- {comment.message}")
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

## Available Tools

The agent uses these LangChain tools:

### File Operations
- `read_file_tool`: Read file contents
- `list_files_tool`: List directory contents
- `find_python_files_tool`: Find and categorize Python files

### Code Analysis
- `extract_signatures_tool`: Extract class/method signatures using AST
- `extract_field_accesses_tool`: Find all field accesses in code
- `extract_test_info_tool`: Analyze test files

### Validators
- `validate_signatures_tool`: Check signature compliance with spec
- `validate_field_access_tool`: Verify field accesses are valid
- `validate_mapping_tool`: Check data model mappings
- `validate_test_quality_tool`: Assess test coverage and quality
- `validate_structure_tool`: Verify project organization

### Reporting
- `create_review_comment_tool`: Record individual issues
- `finalize_review_tool`: Generate final review report

## Issue Categories

- `signature_mismatch`: Method signatures don't match specification
- `field_access_error`: Accessing non-existent fields on objects
- `mapping_incomplete`: Missing required field mappings
- `test_quality`: Test coverage or quality issues
- `structure_issue`: File organization problems
- `missing_implementation`: Required features not implemented
- `type_error`: Type annotation issues
- `general`: Other issues

## Output Format

### Markdown Report

```markdown
# Code Review: MyModule
**Status:** ❌ FAILED

**Summary:** Found issues with method signatures and test coverage.

**Statistics:**
- 🔴 Errors: 2
- 🟡 Warnings: 1
- 🔵 Info: 0

## Issues

### Signature Mismatch

🔴 **[ERROR]** Method 'process_data' has incorrect parameter types
   - File: `src/mymodule/service.py`
   - Line: 45
   - 💡 Suggestion: Change parameter 'data' type from str to dict
```

### JSON Output

```json
{
  "module_name": "MyModule",
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
  "files_reviewed": ["src/mymodule/service.py", "tests/test_mymodule.py"],
  "statistics": {
    "errors": 2,
    "warnings": 1,
    "info": 0,
    "total": 3
  }
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CodeReviewAgent                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   LangChain │    │  LangGraph  │    │   Review    │     │
│  │   ChatOpenAI│───▶│  ReAct Agent│───▶│   Result    │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│                            │                                │
│                            ▼                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                      Tools                            │  │
│  │                                                       │  │
│  │  ┌─────────┐  ┌───────────┐  ┌───────────────────┐   │  │
│  │  │  File   │  │   Code    │  │    Validators     │   │  │
│  │  │  Tools  │  │  Analyzer │  │                   │   │  │
│  │  └─────────┘  └───────────┘  └───────────────────┘   │  │
│  │                                                       │  │
│  │  ┌───────────────────────────────────────────────┐   │  │
│  │  │              Report Tools                      │   │  │
│  │  └───────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Development

### Running Tests

```bash
# Test tools without LLM
python -m openhands.agenthub.langgraph_reviewer_agent.runner \
    --spec test_data/module_M4/M4.md \
    --code test_data/module_M4/M4_v1.1_run3_after_CR_1 \
    --module VacancyService \
    --test-tools

# Full integration test
python test_data/test_review_agent.py
```

### Adding New Tools

1. Create the tool function in the appropriate file under `tools/`
2. Use the `@tool` decorator from `langchain_core.tools`
3. Add the tool to `tools/__init__.py`
4. The agent will automatically include it

Example:

```python
from langchain_core.tools import tool

@tool
def my_validation_tool(file_path: str, pattern: str) -> str:
    """Validate something in the code.

    Args:
        file_path: Path to the file.
        pattern: Pattern to check.

    Returns:
        JSON string with validation results.
    """
    # Implementation
    return json.dumps({"valid": True})
```
