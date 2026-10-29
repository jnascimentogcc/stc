def greet(name):
    """Build a greeting for a person."""
    return f"Hello, {name}!"


class Greeter:
    def run(self, name):
        return greet(name)
