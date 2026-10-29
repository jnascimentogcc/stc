from fastapi import FastAPI

app = FastAPI()


def greet(name):
    """Build a greeting for a person."""
    return f"Hello, {name}!"


class Greeter:
    def run(self, name):
        return greet(name)


@app.get("/greet/{name}")
def greet_endpoint(name: str):
    return {"message": Greeter().run(name)}
