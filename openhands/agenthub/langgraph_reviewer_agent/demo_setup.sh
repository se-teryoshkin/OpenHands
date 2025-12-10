#!/bin/bash
# Demo setup script for LangGraph Reviewer Agent
# This script creates a sample project and runs the reviewer on it

set -e

echo "=========================================="
echo "LangGraph Reviewer Agent - Demo Setup"
echo "=========================================="
echo ""

# Check for API key
if [ -z "$OPENAI_API_KEY" ]; then
    echo "ERROR: OPENAI_API_KEY environment variable not set"
    echo "Please set it with: export OPENAI_API_KEY='your-key'"
    exit 1
fi

# Create demo directory
DEMO_DIR="/tmp/reviewer_demo_$(date +%s)"
echo "Creating demo project in: $DEMO_DIR"
mkdir -p "$DEMO_DIR"

# Create sample Python files
cat > "$DEMO_DIR/calculator.py" << 'EOF'
"""A simple calculator module."""

def add(a, b):
    """Add two numbers."""
    return a + b

def subtract(a, b):
    """Subtract b from a."""
    return a - b

def multiply(a, b):
    """Multiply two numbers."""
    return a * b

def divide(a, b):
    """Divide a by b."""
    # BUG: No check for division by zero!
    return a / b

def power(a, b):
    """Raise a to the power of b."""
    # BUG: No handling for negative exponents
    return a ** b
EOF

cat > "$DEMO_DIR/test_calculator.py" << 'EOF'
"""Tests for calculator module."""
from calculator import add, subtract, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 5) == -5

def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0
    assert multiply(-2, 3) == -6

# ISSUE: Missing tests for divide() and power()!
EOF

cat > "$DEMO_DIR/README.md" << 'EOF'
# Calculator Demo

A simple calculator implementation for testing the reviewer agent.

## Known Issues

This demo intentionally contains several issues:
1. Division by zero not handled
2. Negative exponents not handled
3. Missing tests for divide() and power()
4. No input validation

The reviewer should identify these issues.
EOF

echo "✓ Created demo project"
echo ""

# Show what was created
echo "Demo files created:"
ls -la "$DEMO_DIR"
echo ""

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "=========================================="
echo "Running LangGraph Reviewer Agent..."
echo "=========================================="
echo ""

# Run the reviewer
python3 "$SCRIPT_DIR/test_reviewer_standalone.py" \
    "$DEMO_DIR" \
    --focus "bugs and testing" \
    --max-steps 10

echo ""
echo "=========================================="
echo "Demo Complete!"
echo "=========================================="
echo ""
echo "The demo project is located at: $DEMO_DIR"
echo ""
echo "You can review the files:"
echo "  cat $DEMO_DIR/calculator.py"
echo "  cat $DEMO_DIR/test_calculator.py"
echo ""
echo "Or run the reviewer again with different options:"
echo "  python3 $SCRIPT_DIR/test_reviewer_standalone.py $DEMO_DIR --focus security"
echo ""
echo "To clean up:"
echo "  rm -rf $DEMO_DIR"
echo ""

