# Running Code Review on Your Project

## Summary

I've created a script (`run_review_with_spec.py`) to run the LangGraph Reviewer Agent on your codebase with your specification file, but the required dependencies need to be installed first.

## Your Review Setup

- **Code Directory**: `tmp_data/deploy-20251129-074244-703946/source_code`
- **Specification**: `tmp_data/spec_cli/architecture.md`
- **Specification Language**: Russian (7 modules: DocumentDownloader, DocumentIndexer, SearchEngine, etc.)

## Required Steps

### 1. Install Dependencies

```bash
# Option A: Install OpenHands with langgraph agent support
cd /Users/ngc436/Documents/projects/OpenHands
pip install -e ".[langgraph_agent]"

# Option B: Install individual packages if needed
pip install litellm langchain langchain-core langgraph browsergym
```

### 2. Set API Key

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

### 3. Run the Review

```bash
cd /Users/ngc436/Documents/projects/OpenHands

# Basic run
python3 run_review_with_spec.py \
  tmp_data/deploy-20251129-074244-703946/source_code \
  tmp_data/spec_cli/architecture.md

# With options
python3 run_review_with_spec.py \
  tmp_data/deploy-20251129-074244-703946/source_code \
  tmp_data/spec_cli/architecture.md \
  --model gpt-4o \
  --max-steps 30
```

## What the Review Will Check

Based on your `architecture.md` specification, the agent will verify:

### Module M1: DocumentDownloader
- URL resolver implementation
- Format validator (doc, docx, pdf, etc.)
- Downloader with download_tool
- Temp file manager
- Document downloader coordinator

### Module M2: DocumentIndexer
- Document parser using RAG component
- Metadata normalizer
- Index builder
- Indexer controller

### Module M3: SearchEngine
- Query processor using RAG
- Result builder
- No results responder

### Module M4: UserInterfaceAdapter
- UI input handler
- UI output formatter (Russian language)
- UI session manager
- UI messages provider

### Module M5: Logger
- UTF-8 logging with timestamps

### Module M6: ProgressReporter
- Progress message formatting (Russian)

### Module M7: PlatformSupport
- Path utilities (cross-platform)
- File handler
- Directory manager

## Expected Output

The agent will provide:

1. **Summary** - Overview of compliance with specification
2. **Critical Issues** - Missing modules, broken functionality
3. **Major Issues** - Incomplete implementations, design problems
4. **Minor Issues** - Code quality, documentation gaps
5. **Recommendation** - APPROVE, REQUEST_CHANGES, or NEEDS_DISCUSSION

## Options

```bash
--model gpt-4o        # LLM model to use (default)
--max-steps 30        # Maximum review steps (default: 30)
--no-tests           # Skip running tests
```

## Troubleshooting

### Missing Module Errors
If you see `ModuleNotFoundError: No module named 'X'`:
```bash
pip install X
```

### API Key Not Set
```bash
export OPENAI_API_KEY="sk-..."
```

### Review Takes Too Long
Increase max-steps or use a faster model:
```bash
python3 run_review_with_spec.py ... --max-steps 50 --model gpt-4o-mini
```

## Files Created

1. **`run_review_with_spec.py`** - Main review script (adapted from test code)
2. **`run_reviewer_with_spec.py`** - Original standalone version (requires full install)
3. **`REVIEWER_README.md`** - General documentation
4. **`example_spec.md`** - Specification template
5. **`reviewer_test_project/`** - Test project for verification

## Next Steps

1. Install dependencies (see above)
2. Set OPENAI_API_KEY
3. Run the review command
4. Review the output and address any issues found

The review will analyze your Python code against the Russian specification and provide detailed feedback on implementation compliance.
