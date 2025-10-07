from models.product import Product
from utils.string_helpers import format_title


class Store:
    def __init__(self, name):
        self.name = name
        self.products = []

    def add_product(self, product):
        """Add a product to the store"""
        self.products.append(product)

    def get_total_value(self):
        """Calculate total value of all products"""
        total = 0
        for product in self.products:
            total += product.get_total()
        return total

    def display_inventory(self):
        """Display store inventory"""
        output = format_title(f"{self.name} Inventory")
        output += "\n\nProducts:\n"
        for i, product in enumerate(self.products, 1):
            output += f"{i}. {product.get_description()}\n"
        output += f"\nTotal Value: ${self.get_total_value():.2f}"
        return output

