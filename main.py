"""Runner: start the FastAPI app from the `src` package."""

from src.api import app


if __name__ == "__main__":
    import uvicorn

    # Run using the ASGI app object directly
    uvicorn.run(app, host="0.0.0.0", port=8000)