"""Conversation history in SQLite, sharing the Node server's database file.

Phase 4 needs only `remove_document`; the rest of the store arrives in Phase 6.
The schema and its migrations match server/conversations.js exactly.
"""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from app.errors import ApiError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, documentId TEXT NOT NULL, title TEXT NOT NULL,
  createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, conversationId TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]',
  citationNote TEXT, error TEXT, createdAt TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed', 'stopped'))
);
CREATE INDEX IF NOT EXISTS conversations_document ON conversations(documentId);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversationId);
"""


def require_owner(owner_id) -> None:
    if not isinstance(owner_id, str) or not owner_id.startswith("user_"):
        raise ApiError(401, "Sign in to continue.")


class ConversationStore:
    def __init__(self, path):
        self.path = Path(path)
        self._schema_ready = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")  # Deleting a conversation deletes its turns.
        connection.execute("PRAGMA busy_timeout = 5000")
        if not self._schema_ready:
            with self._lock:
                if not self._schema_ready:
                    self._migrate(connection)
                    self._schema_ready = True
        return connection

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        db.executescript(_SCHEMA)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(turns)")}
        if "answerMode" not in columns:
            # Existing questions keep the original balanced behaviour.
            db.execute("ALTER TABLE turns ADD COLUMN answerMode TEXT NOT NULL DEFAULT 'balanced'"
                       " CHECK(answerMode IN ('balanced', 'detailed'))")
        if "ownerId" not in {row["name"] for row in db.execute("PRAGMA table_info(conversations)")}:
            db.execute("ALTER TABLE conversations ADD COLUMN ownerId TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(ownerId, documentId)")
        db.commit()

    def remove_document(self, document_id: str, owner_id: str) -> None:
        require_owner(owner_id)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM conversations WHERE documentId = ? AND ownerId = ?", (document_id, owner_id))
