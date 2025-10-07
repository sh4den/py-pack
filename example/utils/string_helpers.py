def greet(name):
    """Generate a greeting message"""
    return f"Hello, {name}!"


def shout(text):
    """Convert text to uppercase with exclamation"""
    return text.upper() + "!"


def format_title(text):
    """Format text as a title"""
    return "=" * 40 + f"\n{text.center(40)}\n" + "=" * 40

