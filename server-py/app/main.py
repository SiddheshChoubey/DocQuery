"""DocQuery API (Python).

Run from the server-py folder:
    uvicorn app.main:app --port 8000 --reload
"""

import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import ClerkAuth
from app.errors import NoStoreMiddleware, SafeErrorsMiddleware, register_error_handlers

load_dotenv()


def client_origins() -> list[str]:
    raw = os.getenv("CLIENT_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(auth: Optional[ClerkAuth] = None) -> FastAPI:
    origins = client_origins()
    app = FastAPI(title="DocQuery API", docs_url=None, redoc_url=None, openapi_url=None)
    # Every route except the health check depends on this; it returns the owner id.
    app.state.auth = auth or ClerkAuth.from_env(origins)

    # Middleware added last runs first: CORS wraps everything, so even error
    # responses carry CORS headers.
    app.add_middleware(SafeErrorsMiddleware)
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    register_error_handlers(app)

    @app.get("/")
    def health():
        return {"status": "All Good!"}

    return app


app = create_app()
