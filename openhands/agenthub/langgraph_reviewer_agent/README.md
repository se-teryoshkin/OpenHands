# LangGraph Reviewer Agent

A code reviewer agent for OpenHands that uses LangGraph and the ReAct (Reasoning and Acting) pattern to systematically review code changes.

## Overview

The LangGraph Reviewer Agent is designed to:
- Review code changes in a repository
- Run tests and validation commands
- Identify potential issues (bugs, security concerns, style problems)
- Provide actionable feedback and recommendations

## Installation

Install the required LangGraph dependencies:

```bash
pip install openhands-ai[langgraph_agent]
```

Or install manually:

```bash
pip install langchain-core langchain-openai langgraph
```

## Usage

### As a Standalone Tool

You can test the reviewer agent on any code directory:

```bash
python openhands/agenthub/langgraph_reviewer_agent/test_reviewer_standalone.py /path/to/code

# With options
python openhands/agenthub/langgraph_reviewer_agent/test_reviewer_standalone.py \
    /path/to/code \
    --focus security \
    --run-tests \
    --model gpt-4 \
    --max-steps 15
```

### As a Delegate Agent

The reviewer agent can be used as a delegate agent within OpenHands:

```python
from openhands.controller.agent import Agent
from openhands.events.action import AgentDelegateAction

# CodeAct can delegate to the reviewer
action = AgentDelegateAction(
    agent='LangGraphReviewerAgent',
    inputs={
        'task': 'Review the code changes',
        'focus': 'security',  # Optional: focus area
        'run_tests': True,     # Optional: whether to run tests
    }
)
```

## Features

### Review Process

The agent follows a systematic review process:

1. **Context Gathering**: Examines git diffs, file changes, or directory structure
2. **Static Analysis**: Reviews code for:
   - Syntax and logical errors
   - Code quality issues
   - Potential bugs
   - Security vulnerabilities
3. **Dynamic Testing**: Runs tests and validation commands
4. **Reporting**: Provides structured feedback with:
   - Critical issues (bugs, security problems)
   - Major issues (design problems, significant quality concerns)
   - Minor issues (style, documentation)
   - Test results
   - Final recommendation

### Available Tools

The agent has access to:
- `run_command`: Execute shell commands (tests, linters, git commands)
- `read_file`: Read and analyze file contents
- `finish_review`: Complete the review with findings

### Output Format

The agent provides structured output:
- **Summary**: Overview of the review
- **Critical Issues**: Must-fix problems
- **Major Issues**: Significant concerns
- **Minor Issues**: Suggestions for improvement
- **Test Results**: Outcome of running tests
- **Recommendation**: `APPROVE`, `REQUEST_CHANGES`, or `NEEDS_DISCUSSION`

## Configuration

### Agent Configuration

You can configure the agent in `config.toml`:

```toml
[agent.LangGraphReviewerAgent]
# Custom configuration options can be added here
```

### LLM Configuration

The agent uses the standard OpenHands LLM configuration:

```toml
[llm]
model = "gpt-4"
api_key = "your-api-key"
```

## Environment Variables

Required environment variables:
- `OPENAI_API_KEY`: For OpenAI models (GPT-4, etc.)
- Or appropriate API keys for other LLM providers

## Testing

### Unit Tests

Run the unit tests:

```bash
pytest tests/unit/agenthub/langgraph_reviewer_agent/
```

### Integration Tests

Run integration tests (requires API key):

```bash
OPENAI_API_KEY=your-key pytest tests/unit/agenthub/langgraph_reviewer_agent/ -m integration
```

### Standalone Testing

Test on a real code directory:

```bash
export OPENAI_API_KEY=your-key
python openhands/agenthub/langgraph_reviewer_agent/test_reviewer_standalone.py ./my_project
```

## Architecture

The agent is built using:
- **LangGraph**: For building the ReAct agent graph
- **LangChain**: For LLM interactions and tool management
- **OpenHands LLM Adapter**: Bridges OpenHands LLM with LangChain

Key components:
- `langgraph_reviewer_agent.py`: Main agent implementation
- `tools/review_tools.py`: Tool definitions
- `prompts/system_prompt.j2`: System prompt template
- `test_reviewer_standalone.py`: Standalone testing script

## Limitations

- Requires LangGraph dependencies (optional install)
- Currently supports OpenAI models (can be extended to other providers)
- Review quality depends on LLM capabilities
- May require multiple steps for thorough reviews

## Examples

### Example 1: Security Review

```bash
python test_reviewer_standalone.py /path/to/app --focus security
```

### Example 2: Post-Development Review

```bash
python test_reviewer_standalone.py /path/to/feature --run-tests --focus "code quality"
```

### Example 3: Quick Check

```bash
python test_reviewer_standalone.py /path/to/changes --max-steps 5
```

## Contributing

To extend the reviewer agent:

1. Add new tools in `tools/review_tools.py`
2. Update prompts in `prompts/system_prompt.j2`
3. Add configuration options in agent config
4. Add tests in `tests/unit/agenthub/langgraph_reviewer_agent/`

## License

Same as OpenHands project license.

