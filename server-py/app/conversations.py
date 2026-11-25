"""Conversation history in SQLite, sharing the Node server's database file.

Mirrors server/conversations.js: same tables, columns, migrations, limits and
messages. Every read and write is scoped to the signed-in owner.
"""

import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

from app.errors import ApiError
from app.jobs import now_iso

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

RESTART_MESSAGE = "The server restarted before this answer finished. Retry the question."
ANSWER_MODES = ("balanced", "detailed")
_TURN_ID = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$", re.IGNORECASE)


def require_owner(owner_id) -> None:
    if not isinstance(owner_id, str) or not owner_id.startswith("user_"):
        raise ApiError(401, "Sign in to continue.")


def conversation_context(turns: list[dict]) -> list[dict]:
    """Up to the last 6 completed questions and answers, each cut to 2,000 characters."""
    context = []
    for turn in [turn for turn in turns if turn["status"] == "completed"][-6:]:
        context.append({"role": "user", "content": turn["question"][:2000]})
        context.append({"role": "assistant", "content": turn["answer"][:2000]})
    return context


def contextual_query(question: str, history: list[dict]) -> str:
    if not history:
        return question
    recent = "\n".join(f"{message['role']}: {message['content']}" for message in history[-2:])
    return f"Recent conversation for context:\n{recent}\n\nCurrent question: {question}"


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
        if "answerMode" not in {row["name"] for row in db.execute("PRAGMA table_info(turns)")}:
            # Existing questions keep the original balanced behaviour.
            db.execute("ALTER TABLE turns ADD COLUMN answerMode TEXT NOT NULL DEFAULT 'balanced'"
                       " CHECK(answerMode IN ('balanced', 'detailed'))")
        if "ownerId" not in {row["name"] for row in db.execute("PRAGMA table_info(conversations)")}:
            db.execute("ALTER TABLE conversations ADD COLUMN ownerId TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(ownerId, documentId)")
        # One API process owns this store, and a restart cannot resume an old Gemini call.
        db.execute("UPDATE turns SET status = 'stopped', error = ? WHERE status = 'pending'", (RESTART_MESSAGE,))
        db.commit()

    def list(self, document_id: str, owner_id: str) -> list[dict]:
        require_owner(owner_id)
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM conversations WHERE documentId = ? AND ownerId = ?"
                              " ORDER BY updatedAt DESC, rowid DESC", (document_id, owner_id)).fetchall()
        return [dict(row) for row in rows]

    def create(self, document_id: str, owner_id: str) -> dict:
        require_owner(owner_id)
        conversation_id, now = str(uuid.uuid4()), now_iso()
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO conversations (id, documentId, title, createdAt, updatedAt, ownerId)"
                       " VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, document_id, "New chat", now, now, owner_id))
        return self.get(conversation_id, document_id, owner_id)

    def get(self, conversation_id: str, document_id: str, owner_id: str) -> dict:
        require_owner(owner_id)
        with closing(self._connect()) as db:
            conversation = db.execute("SELECT * FROM conversations WHERE id = ? AND documentId = ? AND ownerId = ?",
                                      (conversation_id, document_id, owner_id)).fetchone()
            if conversation is None:
                raise ApiError(404, "Conversation not found for this document.")
            turns = db.execute("SELECT * FROM turns WHERE conversationId = ? ORDER BY rowid",
                               (conversation_id,)).fetchall()
        return {**dict(conversation),
                "turns": [{**dict(turn), "sources": json.loads(turn["sources"])} for turn in turns]}

    def begin_turn(self, conversation_id: str, document_id: str, body: dict, owner_id: str) -> dict:
        answer_mode = body["answerMode"] if "answerMode" in body else "balanced"
        if answer_mode not in ANSWER_MODES:
            raise ApiError(400, "Choose Balanced or Detailed answer mode.")
        turn_id, question, retry = body.get("turnId"), body.get("question"), body.get("retry", False)
        conversation = self.get(conversation_id, document_id, owner_id)
        turns = conversation["turns"]
        if any(turn["status"] == "pending" for turn in turns):
            raise ApiError(409, "An answer is already being generated in this chat.")
        with closing(self._connect()) as db, db:
            if retry:
                last = turns[-1] if turns else None
                if not last or last["id"] != turn_id or last["status"] not in ("failed", "stopped"):
                    raise ApiError(409, "Only the latest failed or stopped question can be retried.")
                # A retry keeps the original question and its saved answer mode.
                db.execute("UPDATE turns SET status = 'pending', error = NULL, answer = '', sources = '[]',"
                           " citationNote = NULL WHERE id = ?", (turn_id,))
            else:
                if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                    raise ApiError(400, "Enter a question of at most 4,000 characters.")
                if not isinstance(turn_id, str) or not _TURN_ID.match(turn_id):
                    raise ApiError(400, "Invalid message ID.")
                if db.execute("SELECT id FROM turns WHERE id = ?", (turn_id,)).fetchone():
                    raise ApiError(409, "This message was already submitted. Reopen the chat to check its status.")
                if len(turns) >= 200:
                    raise ApiError(400, "This conversation has 200 questions. Start a new chat.")
                db.execute("INSERT INTO turns (id, conversationId, question, createdAt, status, answerMode)"
                           " VALUES (?, ?, ?, ?, 'pending', ?)",
                           (turn_id, conversation_id, question.strip(), now_iso(), answer_mode))
            title = conversation["title"] if turns else " ".join(question.split())[:80]
            db.execute("UPDATE conversations SET title = ?, updatedAt = ? WHERE id = ?",
                       (title, now_iso(), conversation_id))
        return self.get(conversation_id, document_id, owner_id)["turns"][-1]

    def finish_turn(self, turn_id: str, *, status: str, answer: str = "", sources: Optional[list] = None,
                    citation_note: Optional[str] = None, error: Optional[str] = None) -> None:
        with closing(self._connect()) as db, db:
            # Only a pending turn can finish, so a late result never overwrites a stopped one.
            db.execute("UPDATE turns SET status = ?, answer = ?, sources = ?, citationNote = ?, error = ?"
                       " WHERE id = ? AND status = 'pending'",
                       (status, answer, json.dumps(sources or [], ensure_ascii=False), citation_note, error, turn_id))
            db.execute("UPDATE conversations SET updatedAt = ?"
                       " WHERE id = (SELECT conversationId FROM turns WHERE id = ?)", (now_iso(), turn_id))

    def remove(self, conversation_id: str, document_id: str, owner_id: str) -> None:
        conversation = self.get(conversation_id, document_id, owner_id)
        if any(turn["status"] == "pending" for turn in conversation["turns"]):
            raise ApiError(409, "Stop the active answer before deleting this chat.")
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM conversations WHERE id = ? AND documentId = ?", (conversation_id, document_id))

    def remove_document(self, document_id: str, owner_id: str) -> None:
        require_owner(owner_id)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM conversations WHERE documentId = ? AND ownerId = ?", (document_id, owner_id))
