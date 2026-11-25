"""DocQuery API (Python).

Run from the server-py folder:
    uvicorn app.main:app --port 8000 --reload
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.errors import register_error_handlers

load_dotenv()


def client_origins() -> list[str]:
    raw = os.getenv("CLIENT_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="DocQuery API", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=client_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)

    @app.get("/")
    def health():
        return {"status": "All Good!"}

    return app


app = create_app()
