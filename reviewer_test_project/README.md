# Test Project for LangGraph Reviewer

This is a simple test project to verify that the LangGraph Reviewer Agent is working correctly.

## Contents

- `calculator.py` - A simple calculator implementation
- `test_calculator.py` - Unit tests for the calculator
- `test_spec.md` - Specification file for the reviewer

## How to Test the Reviewer

From the OpenHands root directory:

```bash
# Run the review
python3 run_reviewer_with_spec.py reviewer_test_project reviewer_test_project/test_spec.md

# Or use the demo script
./demo_reviewer.sh reviewer_test_project reviewer_test_project/test_spec.md
```

## What to Expect

The reviewer should:
1. Read the specification
2. Examine the calculator.py file
3. Check the tests
4. Run the tests (if pytest is installed)
5. Provide feedback on:
   - Whether all required components are present
   - Code quality
   - Test coverage
   - Any issues found

## Expected Result

The reviewer should likely **APPROVE** this simple project since:
- All required methods are implemented
- Error handling is present
- Tests cover all functionality
- Code follows best practices
- Documentation is adequate

## Running Tests Manually

If you want to run the tests yourself:

```bash
cd reviewer_test_project
python -m pytest test_calculator.py -v
```

## Modifying for Testing

You can introduce issues to see how the reviewer responds:

### Example 1: Missing functionality
Remove the `multiply` method from calculator.py

### Example 2: Failing test
Change one of the assertions to be incorrect

### Example 3: Poor code quality
Remove docstrings or violate PEP 8

Then re-run the reviewer to see how it catches these issues!


