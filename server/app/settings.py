"""Configuration shared by the API and the Celery worker."""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

# uploads/ and data/ live inside the backend folder (both are git-ignored).
STORAGE_ROOT = BACKEND_DIR
DATABASE_PATH = STORAGE_ROOT / "data" / "conversations.sqlite"

VALKEY_URL = os.getenv("VALKEY_URL", "redis://localhost:6379/0")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "pdf-rag-gemini-embedding-2"


def gemini_api_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY in server/.env before starting.")
    return key
