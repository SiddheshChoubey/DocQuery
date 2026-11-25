"""Upload job records, stored in the same SQLite file as conversations.

BullMQ kept finished jobs in Valkey and the API read them for upload status and
document deletion. Celery does not keep records like that, so the API creates a
row here when it queues a job and the worker updates it as indexing runs.
Pasted text is not stored here; it travels only in the queued task.
"""

import json
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATES = ("waiting", "active", "completed", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_jobs (
  id TEXT PRIMARY KEY,
  ownerId TEXT NOT NULL,
  documentId TEXT NOT NULL,
  sourceType TEXT NOT NULL CHECK(sourceType IN ('pdf', 'text')),
  filename TEXT NOT NULL,
  path TEXT,
  uploadedAt TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('waiting', 'active', 'completed', 'failed')),
  progress TEXT,
  error TEXT,
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS upload_jobs_document ON upload_jobs(ownerId, documentId);
"""


def now_iso() -> str:
    """Current UTC time in the same format as JavaScript's toISOString()."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def failure_message(reason: str = "") -> str:
    """Turn a raw indexing error into a safe message (mirrors upload-status.js)."""
    checks = [
        (r"429|quota|RESOURCE_EXHAUSTED",
         "Gemini quota was reached while indexing. Wait for your quota to reset, then upload again."),
        (r"API.key|401|403|permission|UNAUTHENTICATED",
         "Gemini rejected the request. Check your API key and model access."),
        (r"404|not found|not supported",
         "A required model or resource was unavailable. Check the worker and Gemini model access."),
        (r"No extractable text", "This PDF has no extractable text. Scanned PDFs need OCR first."),
        (r"Invalid PDF|password|encrypted", "This PDF could not be read. Try an unencrypted, text-based PDF."),
    ]
    for pattern, message in checks:
        if re.search(pattern, reason or "", re.IGNORECASE):
            return message
    return "Indexing failed. Check the PDF worker, Gemini access, and Qdrant, then upload again."


class UploadJobs:
    def __init__(self, path):
        self.path = Path(path)
        self._schema_ready = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        # The table is created on first use, so importing the app never touches the database.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        if not self._schema_ready:
            with self._lock:
                if not self._schema_ready:
                    # WAL lets the API read job progress while the worker writes it.
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(_SCHEMA)
                    self._schema_ready = True
        return connection

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[dict]:
        if row is None:
            return None
        job = dict(row)
        job["progress"] = json.loads(job["progress"]) if job["progress"] else None
        return job

    def create(self, *, job_id: str, owner_id: str, document_id: str, source_type: str,
               filename: str, path: Optional[str], uploaded_at: str) -> dict:
        now = now_iso()
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO upload_jobs (id, ownerId, documentId, sourceType, filename, path, uploadedAt,"
                " state, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)",
                (job_id, owner_id, document_id, source_type, filename, path, uploaded_at, now, now))
        return self.get(job_id)

    def get(self, job_id: str) -> Optional[dict]:
        with closing(self._connect()) as db:
            return self._row(db.execute("SELECT * FROM upload_jobs WHERE id = ?", (job_id,)).fetchone())

    def update(self, job_id: str, *, state: Optional[str] = None, progress: Optional[dict] = None,
               error: Optional[str] = None) -> None:
        if state is not None and state not in STATES:
            raise ValueError(f"Unknown job state: {state}")
        fields, values = ["updatedAt = ?"], [now_iso()]
        if state is not None:
            fields.append("state = ?")
            values.append(state)
        if progress is not None:
            fields.append("progress = ?")
            values.append(json.dumps(progress))
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        with closing(self._connect()) as db, db:
            db.execute(f"UPDATE upload_jobs SET {', '.join(fields)} WHERE id = ?", (*values, job_id))

    def remove(self, job_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM upload_jobs WHERE id = ?", (job_id,))

    def for_document(self, owner_id: str, document_id: str) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM upload_jobs WHERE ownerId = ? AND documentId = ? ORDER BY createdAt",
                              (owner_id, document_id)).fetchall()
        return [self._row(row) for row in rows]
