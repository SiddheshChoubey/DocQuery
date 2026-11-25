"""Gemini embeddings and Qdrant storage, called through their REST APIs.

The Node version already bypassed most of LangChain here: it overrode the
embedding calls to send one request per chunk with Embedding 2's text prefixes,
and talked to Qdrant's REST API directly for listing and deleting. Calling both
REST APIs with httpx keeps the exact request formats with no extra libraries.
"""

import time
from typing import Callable, Iterator, Optional
from urllib.parse import quote

import httpx

from app import settings

EMBEDDING_MODEL = "gemini-embedding-2"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"

# Embedding 2 uses text prefixes instead of the older taskType parameter.
# These must stay identical to the Node version or search over existing documents degrades.
QUERY_PREFIX = "task: question answering | query: "
DOCUMENT_PREFIX = "title: none | text: "

_RETRYABLE = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    """A Gemini request failed. The message never includes the API key or request body."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class GeminiEmbeddings:
    def __init__(self, api_key: Optional[str] = None, client: Optional[httpx.Client] = None,
                 max_retries: int = 2, sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key or settings.gemini_api_key()
        self.client = client or httpx.Client(timeout=60)
        self.max_retries = max_retries
        self.sleep = sleep

    def embed_query(self, text: str) -> list[float]:
        return self._embed(QUERY_PREFIX + text)

    def embed_document(self, text: str) -> list[float]:
        return self._embed(DOCUMENT_PREFIX + text)

    def _embed(self, text: str) -> list[float]:
        url = f"{GEMINI_API_URL}/models/{EMBEDDING_MODEL}:embedContent"
        body = {"content": {"parts": [{"text": text}]}}
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                response = self.client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            except httpx.TransportError:
                if last:
                    raise GeminiError("Gemini embedding request could not reach the API.") from None
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 200:
                return response.json()["embedding"]["values"]
            if response.status_code in _RETRYABLE and not last:
                self.sleep(2 ** attempt)
                continue
            raise GeminiError(f"Gemini embedding request failed ({response.status_code} {_error_status(response)})",
                              response.status_code)
        raise AssertionError("unreachable")


def _error_status(response: httpx.Response) -> str:
    """Gemini's short error code, e.g. RESOURCE_EXHAUSTED (never the full error body)."""
    try:
        return str(response.json()["error"]["status"])
    except Exception:
        return response.reason_phrase or "error"


class QdrantError(Exception):
    pass


class QdrantStore:
    def __init__(self, url: Optional[str] = None, collection: str = settings.QDRANT_COLLECTION,
                 client: Optional[httpx.Client] = None):
        self.url = (url or settings.QDRANT_URL).rstrip("/")
        self.collection = collection
        self.client = client or httpx.Client(timeout=30)

    def _collection_url(self, suffix: str = "") -> str:
        return f"{self.url}/collections/{quote(self.collection, safe='')}{suffix}"

    def ensure_collection(self, vector_size: int) -> None:
        """Create the collection on first use, like LangChain JS did."""
        response = self.client.get(self._collection_url())
        if response.status_code == 404:
            response = self.client.put(self._collection_url(),
                                       json={"vectors": {"size": vector_size, "distance": "Cosine"}})
            if response.status_code == 409:  # Created by another process in the meantime.
                return
        if not response.is_success:
            raise QdrantError("Unable to prepare the Qdrant collection.")

    def upsert(self, points: list[dict], batch_size: int = 64) -> None:
        for start in range(0, len(points), batch_size):
            response = self.client.put(self._collection_url("/points?wait=true"),
                                       json={"points": points[start:start + batch_size]})
            if not response.is_success:
                raise QdrantError("Unable to save document vectors to Qdrant.")

    def scroll(self, payload_fields: list[str], filter: Optional[dict] = None) -> Iterator[dict]:
        """Yield every point's selected payload fields (never vectors), page by page."""
        offset = None
        while True:
            body = {"limit": 256, "with_vector": False, "with_payload": payload_fields}
            if offset is not None:
                body["offset"] = offset
            if filter:
                body["filter"] = filter
            response = self.client.post(self._collection_url("/points/scroll"), json=body, timeout=10)
            if response.status_code == 404:
                return  # Nothing has been indexed yet.
            if not response.is_success:
                raise QdrantError("Unable to list indexed PDFs from Qdrant.")
            result = response.json()["result"]
            yield from result["points"]
            offset = result.get("next_page_offset")
            if offset is None:
                return

    def delete(self, filter: dict) -> None:
        response = self.client.post(self._collection_url("/points/delete?wait=true"), json={"filter": filter},
                                    timeout=10)
        if not response.is_success:
            raise QdrantError("Unable to remove document vectors.")
