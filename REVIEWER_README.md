# LangGraph Reviewer Agent - Standalone Script

Run the LangGraph Reviewer Agent on any repository with a specification file to guide the review.

## Quick Start

```bash
# 1. Install OpenHands in development mode with all dependencies
pip install -e ".[langgraph_agent]"

# 2. Set API key
export OPENAI_API_KEY="your-key"

# 3. Run review
python3 run_review_with_spec.py <repo_folder> <spec_file.md>
```

## Installation

If you encounter missing dependencies, install them:

```bash
# Core dependencies
pip install litellm langchain langchain-core langgraph

# Optional (for full functionality)
pip install browsergym
```

## Usage

```bash
python3 run_reviewer_with_spec.py <repo_dir> <spec_file.md> [options]

Required:
  repo_dir              Path to repository directory
  spec_file             Path to specification .md file

Options:
  --model MODEL         LLM model (default: gpt-4o)
  --max-steps N         Maximum steps (default: 20)
  --no-tests           Skip running tests
  --verbose            Enable verbose logging
```

## Examples

```bash
# Basic review
python3 run_reviewer_with_spec.py ./my-project ./spec.md

# With options
python3 run_reviewer_with_spec.py ./my-project ./spec.md --model gpt-4o --max-steps 30 --verbose

# Test with sample project
python3 run_reviewer_with_spec.py reviewer_test_project reviewer_test_project/test_spec.md
```

## Creating a Specification File

Your specification file should be a Markdown (.md) file that includes:

1. **Project Overview** - What the code should do
2. **Required Components** - Features/modules that must be present
3. **Code Quality Standards** - Style guides, testing requirements
4. **Security Requirements** - Security considerations
5. **Review Focus Areas** - Specific things to check for

See `example_spec.md` for a complete template.

## Review Output

The agent provides:

- **Summary** - Overview of findings
- **Critical Issues** 🔴 - Security vulnerabilities, breaking bugs
- **Major Issues** 🟡 - Missing features, design problems
- **Minor Issues** 🟢 - Style issues, documentation gaps
- **Test Results** 🧪 - Output from tests/linters
- **Recommendation** - APPROVE, REQUEST_CHANGES, or NEEDS_DISCUSSION

## Exit Codes

- `0` - APPROVE (review passed)
- `1` - REQUEST_CHANGES or NEEDS_DISCUSSION (issues found)
- `2` - ERROR or INCOMPLETE (review failed)
- `130` - User interrupted

## CI/CD Integration

### GitHub Actions
```yaml
- name: AI Code Review
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: python3 run_reviewer_with_spec.py . ./spec.md
```

### GitLab CI
```yaml
review:
  script:
    - python3 run_reviewer_with_spec.py . ./spec.md
```

## Files

- `run_reviewer_with_spec.py` - Main script
- `example_spec.md` - Specification template
- `reviewer_test_project/` - Test project for verification
