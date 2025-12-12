# ✅ Tool Calling Success - Code Review Working!

## Overview
Successfully configured the LangGraph Reviewer Agent to work with custom LLM (Qwen/Qwen3-Coder-480B-A35B-Instruct) for automated code reviews against specifications.

## What Was Fixed

### 1. **Tool Call Format Conversion (LiteLLM ↔ LangChain)**

**Problem:** LiteLLM returns tool calls in OpenAI format, but LangChain expects its own format.

**Solution:** Added bidirectional conversion in `openhands/llm/langchain_adapter.py`:

- **From LiteLLM to LangChain** (line ~129-147):
  ```python
  # OpenAI format: {id, type: 'function', function: {name, arguments}}
  # Convert to LangChain: {name, args, id, type: 'tool_call'}
  ```

- **From LangChain to OpenAI** (line ~70-88):
  ```python
  # LangChain format: {name, args, id, type: 'tool_call'}
  # Convert back to OpenAI for API calls
  ```

### 2. **System Prompt Enhancement**

**Problem:** Model wasn't clear about working directory context.

**Solution:** Updated `openhands/agenthub/langgraph_reviewer_agent/prompts/system_prompt.j2`:
```
**IMPORTANT: You are already in the code repository directory.
All commands and file reads will be executed from this directory.
Start by listing files with `ls -la` or `find .` to see what's available.**
```

### 3. **Model Selection**

**Problem:** Initial vLLM server had parser compatibility issues.

**Solution:** Switched to cloud-hosted Qwen/Qwen3-Coder-480B-A35B-Instruct which natively supports function calling.

## How to Use

### Command
```bash
cd /Users/ngc436/Documents/projects/OpenHands

export OPENAI_API_KEY="<your_key>"
export OPENAI_API_BASE="https://foundation-models.api.cloud.ru/v1"

poetry run python run_review_with_spec.py \
    <code_directory> \
    <specification_file.md> \
    --model "openai/Qwen/Qwen3-Coder-480B-A35B-Instruct" \
    --max-steps 40
```

### Example
```bash
poetry run python run_review_with_spec.py \
    tmp_data/deploy-20251129-074244-703946/source_code \
    tmp_data/spec_cli/architecture.md \
    --model "openai/Qwen/Qwen3-Coder-480B-A35B-Instruct" \
    --max-steps 40
```

## Results

The review successfully:
- ✅ Listed repository structure
- ✅ Read multiple files
- ✅ Compared code against specification
- ✅ Identified critical/major/minor issues
- ✅ Provided actionable recommendations

### Sample Review Output

**Critical Issues Found:**
1. Missing external component integrations (download_tool, rag)
2. Incomplete core functionality (placeholder implementations)
3. No orchestration layer (missing main application file)
4. Insufficient format validation

**Major Issues Found:**
1. Missing error handling framework
2. Incomplete temp manager
3. Progress reporting not integrated
4. Incomplete logging implementation

**Minor Issues Found:**
1. Mixed English/Russian comments
2. Limited type hinting
3. Basic documentation
4. No configuration management

**Recommendation:** REQUEST_CHANGES

## Technical Details

### Files Modified

1. `/Users/ngc436/Documents/projects/OpenHands/openhands/llm/langchain_adapter.py`
   - Added `bind_tools()` method
   - Fixed tool call format conversion (both directions)
   - Added JSON parsing for tool arguments

2. `/Users/ngc436/Documents/projects/OpenHands/openhands/agenthub/langgraph_reviewer_agent/prompts/system_prompt.j2`
   - Enhanced with working directory context
   - Clarified tool usage instructions

3. `/Users/ngc436/Documents/projects/OpenHands/run_review_with_spec.py`
   - Standalone script for specification-based reviews
   - Handles code directory and spec file inputs
   - Executes tools with proper working directory

### Key Insights

1. **LiteLLM/OpenAI Format:**
   ```json
   {
     "id": "chatcmpl-tool-xxx",
     "type": "function",
     "function": {
       "name": "run_command",
       "arguments": "{\"command\": \"ls -la\"}"
     }
   }
   ```

2. **LangChain Format:**
   ```json
   {
     "name": "run_command",
     "args": {"command": "ls -la"},
     "id": "chatcmpl-tool-xxx",
     "type": "tool_call"
   }
   ```

3. **Critical Difference:**
   - OpenAI: `arguments` is a JSON **string**
   - LangChain: `args` is a **dictionary**

## For vLLM Users

If you want to use your own vLLM server instead of the cloud service, ensure it's started with:

```bash
vllm serve <model_name> \
    --enable-auto-tool-choice \
    --tool-call-parser <parser> \
    --host <host> \
    --port <port>
```

Where `<parser>` is one of: `hermes`, `mistral`, `llama3_json`, etc.

## Next Steps

- ✅ Tool calling is working
- ✅ Code reviews are functional
- ✅ Specification-based analysis complete
- 🎯 Ready for production use!

---

**Date:** December 10, 2025
**Status:** ✅ WORKING
**Model:** Qwen/Qwen3-Coder-480B-A35B-Instruct
