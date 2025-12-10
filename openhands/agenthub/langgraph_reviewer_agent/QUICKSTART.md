# Quick Start Guide: LangGraph Reviewer Agent

This guide will help you quickly get started with the LangGraph Reviewer Agent.

## Prerequisites

1. Install OpenHands with LangGraph support:
```bash
pip install openhands-ai[langgraph_agent]
```

2. Set up your API key:
```bash
export OPENAI_API_KEY="your-openai-api-key"
```

## Quick Test

### Step 1: Create a Sample Project

Create a test directory with some code:

```bash
mkdir -p /tmp/test_review
cd /tmp/test_review

# Create a simple Python file
cat > calculator.py << 'EOF'
def add(a, b):
    """Add two numbers."""
    return a + b

def divide(a, b):
    """Divide two numbers."""
    return a / b  # BUG: No zero check!

def multiply(a, b):
    """Multiply two numbers."""
    return a * b
EOF

# Create a test file
cat > test_calculator.py << 'EOF'
from calculator import add, divide, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0

def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0

# Missing test for divide!
EOF
```

### Step 2: Run the Reviewer

```bash
# Navigate to OpenHands directory
cd /path/to/OpenHands

# Run the standalone reviewer
python openhands/agenthub/langgraph_reviewer_agent/test_reviewer_standalone.py \
    /tmp/test_review \
    --focus "bugs and testing" \
    --max-steps 8
```

### Step 3: Review the Output

The agent will:
1. Examine the code files
2. Identify the division by zero bug
3. Note the missing test for the `divide` function
4. Provide a structured review report

Expected output:
```
# Code Review Summary

Found potential issues in the calculator implementation.

## Critical Issues
- Division by zero not handled in divide() function (calculator.py)

## Major Issues
- Missing test coverage for divide() function
- No input validation in any functions

## Minor Issues
- Could add type hints for better documentation
- Consider adding docstring examples

## Test Results
Not all functions are tested. Missing tests for: divide

## Recommendation
**REQUEST_CHANGES**
```

## Command-Line Options

```bash
# Basic review
python test_reviewer_standalone.py /path/to/code

# Focus on specific area
python test_reviewer_standalone.py /path/to/code --focus security

# Run tests during review
python test_reviewer_standalone.py /path/to/code --run-tests

# Use different model
python test_reviewer_standalone.py /path/to/code --model gpt-3.5-turbo

# Limit review steps
python test_reviewer_standalone.py /path/to/code --max-steps 5
```

## Understanding Exit Codes

The script exits with different codes based on the review result:
- `0`: APPROVE - Code looks good
- `1`: REQUEST_CHANGES or NEEDS_DISCUSSION - Issues found
- `2`: ERROR or INCOMPLETE - Review failed

## Integration with OpenHands

To use the reviewer as part of OpenHands workflows:

```python
# In your OpenHands session
from openhands.events.action import AgentDelegateAction

# Delegate to the reviewer
review_action = AgentDelegateAction(
    agent='LangGraphReviewerAgent',
    inputs={
        'task': 'Review the recent code changes',
        'focus': 'security and performance',
        'run_tests': True,
    }
)
```

## Troubleshooting

### Import Error: LangGraph not found

Install the dependencies:
```bash
pip install langchain-core langchain-openai langgraph
```

### API Key Error

Make sure your API key is set:
```bash
export OPENAI_API_KEY="sk-..."
```

### Review Takes Too Long

Reduce max steps or focus the review:
```bash
python test_reviewer_standalone.py /path/to/code --max-steps 5 --focus "critical bugs"
```

## Next Steps

- Read the full [README.md](README.md) for detailed documentation
- Check [prompts/system_prompt.j2](prompts/system_prompt.j2) to customize review guidelines
- Add custom tools in [tools/review_tools.py](tools/review_tools.py)
- Run unit tests: `pytest tests/unit/agenthub/langgraph_reviewer_agent/`

## Tips for Better Reviews

1. **Focus the review**: Use `--focus` to target specific concerns
2. **Limit steps**: Start with `--max-steps 5` for quick checks
3. **Enable tests**: Use `--run-tests` for functional verification
4. **Choose the right model**: GPT-4 for thorough reviews, GPT-3.5 for quick checks
5. **Review incrementally**: Review small changesets for better results

## Example Workflows

### Pre-Commit Review
```bash
# Quick check before committing
python test_reviewer_standalone.py . --max-steps 5 --focus "syntax errors"
```

### Security Audit
```bash
# Focus on security issues
python test_reviewer_standalone.py . --focus "security vulnerabilities" --max-steps 10
```

### Post-Development Review
```bash
# Comprehensive review with tests
python test_reviewer_standalone.py . --run-tests --max-steps 15
```

### Performance Check
```bash
# Focus on performance
python test_reviewer_standalone.py . --focus "performance and efficiency"
```

