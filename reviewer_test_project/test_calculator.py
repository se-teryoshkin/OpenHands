"""Tests for the calculator module."""

import pytest
from calculator import Calculator


def test_add():
    """Test addition."""
    calc = Calculator()
    assert calc.add(2, 3) == 5
    assert calc.add(-1, 1) == 0
    assert calc.add(0, 0) == 0


def test_subtract():
    """Test subtraction."""
    calc = Calculator()
    assert calc.subtract(5, 3) == 2
    assert calc.subtract(3, 5) == -2
    assert calc.subtract(0, 0) == 0


def test_multiply():
    """Test multiplication."""
    calc = Calculator()
    assert calc.multiply(2, 3) == 6
    assert calc.multiply(-2, 3) == -6
    assert calc.multiply(0, 5) == 0


def test_divide():
    """Test division."""
    calc = Calculator()
    assert calc.divide(6, 2) == 3
    assert calc.divide(5, 2) == 2.5

    # Test division by zero
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        calc.divide(5, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


