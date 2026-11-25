"""Configuration shared by the API and the Celery worker."""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

# Folder that holds uploads/ and data/. During the migration this points at the
# Node server's folder (STORAGE_ROOT=../server) so both backends share one copy.
STORAGE_ROOT = (BACKEND_DIR / os.getenv("STORAGE_ROOT", ".")).resolve()
UPLOADS_DIR = STORAGE_ROOT / "uploads"
DATABASE_PATH = STORAGE_ROOT / "data" / "conversations.sqlite"

VALKEY_URL = os.getenv("VALKEY_URL", "redis://localhost:6379/0")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "pdf-rag-gemini-embedding-2"


def gemini_api_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY in server-py/.env before starting.")
    return key
