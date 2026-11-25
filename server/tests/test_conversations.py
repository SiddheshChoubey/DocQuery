import sqlite3
import uuid

import pytest

from app.conversations import (RESTART_MESSAGE, ConversationStore, contextual_query, conversation_context)
from app.errors import ApiError


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "data" / "conversations.sqlite")


def ask(store, chat, question="What is it?", owner="user_a", document="doc", **extra):
    return store.begin_turn(chat["id"], document, {"turnId": str(uuid.uuid4()), "question": question, **extra}, owner)


def raises(status, message, call, *args, **kwargs):
    with pytest.raises(ApiError) as raised:
        call(*args, **kwargs)
    assert (raised.value.status, raised.value.message) == (status, message)


def test_create_list_and_reopen_with_sources(store):
    chat = store.create("doc", "user_a")
    assert chat["title"] == "New chat" and chat["turns"] == [] and chat["ownerId"] == "user_a"
    turn = ask(store, chat, "  Explain   the\n source  ")
    assert turn["status"] == "pending" and turn["answerMode"] == "balanced"
    sources = [{"id": 1, "filename": "a.pdf", "page": 3, "passage": "Café", "cited": True}]
    store.finish_turn(turn["id"], status="completed", answer="Answer [1]", sources=sources)
    reopened = ConversationStore(store.path).get(chat["id"], "doc", "user_a")
    assert reopened["turns"][0]["sources"] == sources
    assert reopened["turns"][0]["status"] == "completed"
    assert [c["id"] for c in store.list("doc", "user_a")] == [chat["id"]]


def test_title_is_first_question_with_whitespace_collapsed(store):
    chat = store.create("doc", "user_a")
    ask(store, chat, "  How   do\tI\nread " + "x" * 100)
    title = store.get(chat["id"], "doc", "user_a")["title"]
    assert title.startswith("How do I read x") and len(title) == 80
    ask_done = store.get(chat["id"], "doc", "user_a")["turns"][0]
    store.finish_turn(ask_done["id"], status="completed", answer="a")
    ask(store, chat, "Second question")
    assert store.get(chat["id"], "doc", "user_a")["title"] == title


def test_newest_chat_is_listed_first(store):
    first = store.create("doc", "user_a")
    second = store.create("doc", "user_a")
    assert [c["id"] for c in store.list("doc", "user_a")] == [second["id"], first["id"]]
    ask(store, first)
    assert store.list("doc", "user_a")[0]["id"] == first["id"]


def test_chats_are_scoped_to_document_and_owner(store):
    chat = store.create("doc", "user_a")
    assert store.list("other-doc", "user_a") == [] and store.list("doc", "user_b") == []
    raises(404, "Conversation not found for this document.", store.get, chat["id"], "other-doc", "user_a")
    raises(404, "Conversation not found for this document.", store.get, chat["id"], "doc", "user_b")


def test_missing_owner_is_refused_and_ownerless_chats_are_hidden(store):
    raises(401, "Sign in to continue.", store.list, "doc", None)
    legacy = store.create("doc", "user_a")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE conversations SET ownerId = NULL WHERE id = ?", (legacy["id"],))
    assert store.list("doc", "user_a") == []
    raises(404, "Conversation not found for this document.", store.get, legacy["id"], "doc", "user_a")


@pytest.mark.parametrize("body, status, message", [
    ({"question": "q", "answerMode": "fast"}, 400, "Choose Balanced or Detailed answer mode."),
    ({"question": "q", "answerMode": None}, 400, "Choose Balanced or Detailed answer mode."),
    ({"question": "   "}, 400, "Enter a question of at most 4,000 characters."),
    ({"question": "x" * 4001}, 400, "Enter a question of at most 4,000 characters."),
    ({"question": 5}, 400, "Enter a question of at most 4,000 characters."),
    ({"question": "q", "turnId": "not-a-uuid"}, 400, "Invalid message ID."),
])
def test_question_validation(store, body, status, message):
    chat = store.create("doc", "user_a")
    body = {"turnId": str(uuid.uuid4()), **body}
    raises(status, message, store.begin_turn, chat["id"], "doc", body, "user_a")
    assert store.get(chat["id"], "doc", "user_a")["turns"] == []


def test_one_pending_answer_and_no_duplicate_ids(store):
    chat = store.create("doc", "user_a")
    turn = ask(store, chat)
    raises(409, "An answer is already being generated in this chat.", ask, store, chat)
    store.finish_turn(turn["id"], status="completed", answer="a")
    raises(409, "This message was already submitted. Reopen the chat to check its status.",
           store.begin_turn, chat["id"], "doc", {"turnId": turn["id"], "question": "again"}, "user_a")


def test_200_question_limit(store):
    chat = store.create("doc", "user_a")
    with sqlite3.connect(store.path) as db:
        db.executemany("INSERT INTO turns (id, conversationId, question, createdAt, status) VALUES (?, ?, 'q', 'now', 'completed')",
                       [(str(uuid.uuid4()), chat["id"]) for _ in range(200)])
    raises(400, "This conversation has 200 questions. Start a new chat.", ask, store, chat)


def test_retry_only_latest_failed_or_stopped_and_keeps_saved_mode(store):
    chat = store.create("doc", "user_a")
    first = ask(store, chat)
    store.finish_turn(first["id"], status="failed", error="x", answer="partial")
    second = ask(store, chat, "More", answerMode="detailed")
    store.finish_turn(second["id"], status="stopped", error="Answer stopped.")
    raises(409, "Only the latest failed or stopped question can be retried.",
           store.begin_turn, chat["id"], "doc", {"turnId": first["id"], "retry": True}, "user_a")
    retried = store.begin_turn(chat["id"], "doc", {"turnId": second["id"], "retry": True, "answerMode": "balanced"},
                               "user_a")
    assert retried["id"] == second["id"] and retried["status"] == "pending"
    assert retried["question"] == "More" and retried["answerMode"] == "detailed"
    assert retried["error"] is None and retried["answer"] == ""
    store.finish_turn(second["id"], status="completed", answer="done")
    raises(409, "Only the latest failed or stopped question can be retried.",
           store.begin_turn, chat["id"], "doc", {"turnId": second["id"], "retry": True}, "user_a")


def test_late_result_cannot_overwrite_a_stopped_turn(store):
    chat = store.create("doc", "user_a")
    turn = ask(store, chat)
    store.finish_turn(turn["id"], status="stopped", error="Answer stopped.")
    store.finish_turn(turn["id"], status="completed", answer="late")
    saved = store.get(chat["id"], "doc", "user_a")["turns"][0]
    assert saved["status"] == "stopped" and saved["answer"] == ""


def test_pending_turns_become_stopped_after_restart(store):
    chat = store.create("doc", "user_a")
    ask(store, chat)
    turn = ConversationStore(store.path).get(chat["id"], "doc", "user_a")["turns"][0]
    assert turn["status"] == "stopped" and turn["error"] == RESTART_MESSAGE


def test_remove_chat_and_document_chats(store):
    chat = store.create("doc", "user_a")
    turn = ask(store, chat)
    raises(409, "Stop the active answer before deleting this chat.", store.remove, chat["id"], "doc", "user_a")
    store.finish_turn(turn["id"], status="completed", answer="a")
    raises(404, "Conversation not found for this document.", store.remove, chat["id"], "doc", "user_b")
    store.remove(chat["id"], "doc", "user_a")
    assert store.list("doc", "user_a") == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    store.create("doc", "user_a")
    other = store.create("doc", "user_b")
    store.remove_document("doc", "user_a")
    assert store.list("doc", "user_a") == [] and [c["id"] for c in store.list("doc", "user_b")] == [other["id"]]


def test_old_database_is_migrated_without_losing_answers(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:  # Schema from before answer modes and owners.
        db.executescript("""
          CREATE TABLE conversations (id TEXT PRIMARY KEY, documentId TEXT NOT NULL, title TEXT NOT NULL,
            createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL);
          CREATE TABLE turns (id TEXT PRIMARY KEY, conversationId TEXT NOT NULL REFERENCES conversations(id)
            ON DELETE CASCADE, question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL
            DEFAULT '[]', citationNote TEXT, error TEXT, createdAt TEXT NOT NULL, status TEXT NOT NULL);
          INSERT INTO conversations VALUES ('c', 'doc', 'Old', 'now', 'now');
          INSERT INTO turns (id, conversationId, question, answer, createdAt, status)
            VALUES ('t', 'c', 'q', 'kept answer', 'now', 'completed');""")
        db.execute("UPDATE conversations SET documentId = 'doc'")
    store = ConversationStore(path)
    store.remove_document("none", "user_a")  # Triggers the migration.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE conversations SET ownerId = 'user_a'")
    turn = store.get("c", "doc", "user_a")["turns"][0]
    assert turn["answer"] == "kept answer" and turn["answerMode"] == "balanced"


def test_context_is_bounded_and_uses_completed_turns_only():
    turns = [{"status": "completed", "question": f"q{index}", "answer": "a" * 3000} for index in range(8)]
    turns += [{"status": status, "question": "skip", "answer": "skip"} for status in ("failed", "stopped", "pending")]
    context = conversation_context(turns)
    assert len(context) == 12
    assert context[0] == {"role": "user", "content": "q2"}
    assert all(len(message["content"]) <= 2000 for message in context)
    assert "skip" not in str(context)


def test_contextual_query():
    history = [{"role": "user", "content": "First"}, {"role": "assistant", "content": "Answer"},
               {"role": "user", "content": "Second"}, {"role": "assistant", "content": "Answer two"}]
    assert contextual_query("Where?", []) == "Where?"
    assert contextual_query("Where?", history) == ("Recent conversation for context:\nuser: Second\n"
                                                   "assistant: Answer two\n\nCurrent question: Where?")
