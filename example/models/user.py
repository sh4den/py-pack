from utils.string_helpers import greet


class User:
    def __init__(self, name, age):
        self.name = name
        self.age = age

    def get_greeting(self):
        """Get a personalized greeting"""
        return greet(self.name)

    def get_info(self):
        """Get user information"""
        return f"{self.name} is {self.age} years old"

    def is_adult(self):
        """Check if user is an adult"""
        return self.age >= 18

