# Example Code Review Specification

## Project Overview
This is an example specification file that guides the LangGraph Reviewer Agent in reviewing your codebase.

## Required Components

### 1. Core Functionality
- [ ] Main application entry point
- [ ] Configuration management
- [ ] Error handling and logging
- [ ] Input validation

### 2. Code Quality Requirements
- All Python code must follow PEP 8 style guide
- Type hints should be used for function signatures
- Docstrings required for all public functions and classes
- Test coverage should be >= 80%

### 3. Security Requirements
- No hardcoded credentials or API keys
- Input sanitization for all user inputs
- Proper error messages (no sensitive info leaked)
- Dependencies should be up-to-date and vulnerability-free

### 4. Testing Requirements
- Unit tests for all core functionality
- Integration tests for main workflows
- All tests must pass
- No skipped tests without justification

### 5. Documentation
- README.md with setup instructions
- API documentation (if applicable)
- Inline comments for complex logic
- Changelog or version history

## Specific Review Focus Areas

### Anti-patterns to Check
- ❌ No mock objects or fake dependencies in production code
- ❌ No commented-out code blocks
- ❌ No TODO comments without tracking
- ❌ No overly complex functions (>50 lines)

### Best Practices to Verify
- ✅ Single Responsibility Principle followed
- ✅ DRY (Don't Repeat Yourself) principle
- ✅ Proper separation of concerns
- ✅ Meaningful variable and function names

## Expected Deliverables
1. All unit tests passing
2. No critical linting errors
3. All required components implemented
4. Documentation up-to-date

## Review Recommendations
- Provide specific line numbers for issues found
- Suggest concrete improvements
- Prioritize critical issues over style preferences
- Include examples of better implementations where applicable
