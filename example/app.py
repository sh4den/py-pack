from models.user import User
from models.product import Product
from services.calculator import Calculator
from services.store import Store
from utils.string_helpers import shout, format_title


def main():
    """Main application entry point"""
    print(format_title("Example Application"))
    print()

    # Create and use a user
    user = User("Alice", 25)
    print(user.get_greeting())
    print(user.get_info())
    print(f"Is adult: {user.is_adult()}")
    print()

    # Use calculator
    calc = Calculator()
    print(shout("Calculator Demo"))
    calc.add_numbers(10, 5)
    calc.multiply_numbers(7, 3)
    calc.calculate_rectangle_area(5, 8)
    print(calc.get_history())
    print()

    # Create a store with products
    store = Store("Tech Shop")
    store.add_product(Product("Laptop", 999.99, 2))
    store.add_product(Product("Mouse", 29.99, 5))
    store.add_product(Product("Keyboard", 79.99, 3))

    print(store.display_inventory())
    print()

    # Apply discount
    print("\nApplying 10% discount to Laptop...")
    store.products[0].apply_discount(10)
    print(store.display_inventory())


if __name__ == "__main__":
    main()
# Utils package

