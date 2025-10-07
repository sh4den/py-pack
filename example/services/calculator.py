from utils.math_helpers import add, multiply, calculate_area


class Calculator:
    def __init__(self):
        self.history = []

    def add_numbers(self, a, b):
        """Add two numbers and store in history"""
        result = add(a, b)
        self.history.append(f"{a} + {b} = {result}")
        return result

    def multiply_numbers(self, a, b):
        """Multiply two numbers and store in history"""
        result = multiply(a, b)
        self.history.append(f"{a} × {b} = {result}")
        return result

    def calculate_rectangle_area(self, width, height):
        """Calculate rectangle area"""
        result = calculate_area(width, height)
        self.history.append(f"Area({width} × {height}) = {result}")
        return result

    def get_history(self):
        """Get calculation history"""
        return "\n".join(self.history)

