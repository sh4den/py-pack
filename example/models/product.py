from utils.math_helpers import multiply


class Product:
    def __init__(self, name, price, quantity=1):
        self.name = name
        self.price = price
        self.quantity = quantity

    def get_total(self):
        """Calculate total price"""
        return multiply(self.price, self.quantity)

    def get_description(self):
        """Get product description"""
        return f"{self.name} - ${self.price} (Qty: {self.quantity})"

    def apply_discount(self, discount_percent):
        """Apply a discount to the price"""
        discount = multiply(self.price, discount_percent / 100)
        self.price = self.price - discount
        return self.price

