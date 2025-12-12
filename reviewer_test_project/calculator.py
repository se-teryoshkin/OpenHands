"""Simple calculator module for testing the reviewer."""


class Calculator:
    """A basic calculator class."""

    def add(self, a, b):
        """Add two numbers."""
        return a + b

    def subtract(self, a, b):
        """Subtract b from a."""
        return a - b

    def multiply(self, a, b):
        """Multiply two numbers."""
        return a * b

    def divide(self, a, b):
        """Divide a by b."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b


def main():
    """Main function to demonstrate calculator."""
    calc = Calculator()
    print(f"5 + 3 = {calc.add(5, 3)}")
    print(f"5 - 3 = {calc.subtract(5, 3)}")
    print(f"5 * 3 = {calc.multiply(5, 3)}")
    print(f"5 / 3 = {calc.divide(5, 3)}")


if __name__ == "__main__":
    main()
