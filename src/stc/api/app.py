from fastapi import FastAPI

from stc import __version__

app = FastAPI(title="STC", version=__version__)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
