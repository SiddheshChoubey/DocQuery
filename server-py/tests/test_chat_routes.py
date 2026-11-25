"""Conversation routes end to end, with fake Gemini and Qdrant (chat-routes, conversations,
streaming, answer-mode and query-rewrite tests from the Node server)."""

import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.conversations import ConversationStore
from app.jobs import UploadJobs
from app.main import create_app
from tests.conftest import ORIGIN
from tests.fake_qdrant import FakeQdrant


class Chunk:
    def __init__(self, content):
        self.content = content


class FakeChatModel:
    """Streams `reply` word by word. Set `hold` to pause mid-answer until released."""

    def __init__(self):
        self.reply = "Synthetic answer [1]."
        self.error = None
        self.hold = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    async def astream(self, messages):
        import asyncio

        self.calls.append(messages)
        if self.error:
            raise self.error
        for index, word in enumerate(self.reply.split(" ")):
            yield Chunk(word if index == 0 else " " + word)
            if self.hold and index == 0:
                self.started.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.01)

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return Chunk(self.reply)


class FakeRewriteModel:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return Chunk("Standalone search query")


class FakeEmbeddings:
    def __init__(self):
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return [0.1]


def meta(document_id, owner="user_a", page=1, **extra):
    return {"documentId": document_id, "ownerId": owner, "fileName": f"{document_id}.pdf", "sourceType": "pdf",
            "uploadedAt": "2026-11-25T10:00:00.000Z", "source": f"uploads/1-2-{document_id}.pdf",
            "loc": {"pageNumber": page}, **extra}


@pytest.fixture
def env(tmp_path, auth, make_token):
    database = tmp_path / "data" / "conversations.sqlite"
    qdrant = FakeQdrant()
    for page in range(1, 7):
        qdrant.add(meta("a", page=page), content=f"Doc A passage {page}")
    qdrant.add(meta("b"), content="Doc B passage")
    qdrant.add(meta("c", owner="user_b"), content="Other user's passage")
    model, rewriter, embeddings = FakeChatModel(), FakeRewriteModel(), FakeEmbeddings()
    app = create_app(auth=auth, jobs=UploadJobs(database), enqueue=lambda *_a: None, storage_root=tmp_path,
                     store=qdrant.store(), conversations=ConversationStore(database),
                     chat_model=model, rewrite_model=rewriter, embeddings=embeddings)
    with TestClient(app, raise_server_exceptions=False) as client:
        def call(method, url, body=None, owner="user_a", stream=True):
            headers = {"Authorization": f"Bearer {make_token(owner)}", "Origin": ORIGIN}
            if stream:
                headers["Accept"] = "application/x-ndjson"
            return client.request(method, url, headers=headers, json=body)

        def chat(document="a", owner="user_a"):
            return call("POST", "/conversations", {"documentId": document}, owner).json()["conversation"]

        def ask(chat_id, question="Explain the source", document="a", owner="user_a", **extra):
            body = {"documentId": document, "turnId": extra.pop("turnId", str(uuid.uuid4())),
                    "question": question, **extra}
            return call("POST", f"/conversations/{chat_id}/messages", body, owner, stream=extra.pop("stream", True))

        yield type("Env", (), {"call": staticmethod(call), "chat": staticmethod(chat), "ask": staticmethod(ask),
                               "model": model, "rewriter": rewriter, "embeddings": embeddings, "qdrant": qdrant,
                               "app": app, "database": database})


def events_of(response):
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert response.headers["x-accel-buffering"] == "no"
    return [json.loads(line) for line in response.text.strip().split("\n")]


# --- Creating, listing and reopening ---

def test_create_list_and_reopen(env):
    assert env.call("POST", "/conversations", {"documentId": "unknown"}).json() == {"message": "This document is not indexed."}
    assert env.call("POST", "/conversations", {"documentId": "c"}).status_code == 404  # Another user's document.
    response = env.call("POST", "/conversations", {"documentId": "a"})
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    chat = response.json()["conversation"]
    assert chat["title"] == "New chat" and chat["turns"] == []
    assert [c["id"] for c in env.call("GET", "/conversations?documentId=a").json()["conversations"]] == [chat["id"]]
    assert env.call("GET", "/conversations?documentId=b").json() == {"conversations": []}
    assert env.call("GET", f"/conversations/{chat['id']}?documentId=b").status_code == 404
    assert env.call("GET", f"/conversations/{chat['id']}?documentId=a", owner="user_b").status_code == 404
    assert env.call("GET", f"/conversations/{chat['id']}?documentId=a").json()["conversation"]["id"] == chat["id"]


@pytest.mark.parametrize("url", ["/conversations", "/conversations?documentId=", "/conversations?documentId=" + "x" * 201])
def test_document_id_is_required(env, url):
    response = env.call("GET", url)
    assert response.status_code == 400 and response.json() == {"message": "Select a document."}


# --- Answering ---

def test_streamed_answer_events_and_saved_result(env):
    chat = env.chat()
    events = events_of(env.ask(chat["id"]))
    assert events[0]["type"] == "started"
    assert events[1]["type"] == "sources" and len(events[1]["sources"]) == 2  # Balanced: 2 passages.
    assert events[1]["sources"][0] == {"id": 1, "filename": "a.pdf", "page": 1, "passage": "Doc A passage 1"}
    assert "".join(event["text"] for event in events if event["type"] == "delta") == "Synthetic answer [1]."
    done = events[-1]
    assert done["type"] == "done"
    turn = done["conversation"]["turns"][0]
    assert turn["status"] == "completed" and turn["answer"] == "Synthetic answer [1]."
    assert [source["cited"] for source in turn["sources"]] == [True, False]
    assert turn["citationNote"] is None
    assert done["conversation"]["title"] == "Explain the source"
    reopened = env.call("GET", f"/conversations/{chat['id']}?documentId=a").json()["conversation"]
    assert reopened["turns"][0]["sources"][0]["passage"] == "Doc A passage 1"


def test_answers_only_use_the_selected_document(env):
    chat = env.chat("b")
    turn = events_of(env.ask(chat["id"], document="b"))[-1]["conversation"]["turns"][0]
    assert [source["passage"] for source in turn["sources"]] == ["Doc B passage"]


def test_non_streaming_request_returns_the_conversation(env):
    chat = env.chat()
    response = env.ask(chat["id"], stream=False)
    assert response.headers["content-type"] == "application/json"
    assert response.json()["conversation"]["turns"][0]["status"] == "completed"


def test_detailed_mode_retrieves_five_and_adds_instructions(env):
    chat = env.chat()
    turn = events_of(env.ask(chat["id"], answerMode="detailed"))[-1]["conversation"]["turns"][0]
    assert len(turn["sources"]) == 5 and turn["answerMode"] == "detailed"
    assert "Answer detail: Detailed." in env.model.calls[-1][0][1]


def test_follow_up_uses_rewritten_query_and_saved_history(env):
    chat = env.chat()
    env.ask(chat["id"], "First question")
    assert env.rewriter.calls == []  # The first question isn't rewritten.
    assert env.embeddings.queries == ["First question"]
    env.ask(chat["id"], "What about it?")
    assert env.embeddings.queries[-1] == "Standalone search query"
    messages = env.model.calls[-1]
    assert messages[1:] == [("human", "First question"), ("ai", "Synthetic answer [1]."), ("human", "What about it?")]


def test_document_removed_from_index_fails_with_message(env):
    chat = env.chat()
    env.qdrant.points = []  # The document is gone from Qdrant after the chat was opened.
    turn = events_of(env.ask(chat["id"]))[-1]["conversation"]["turns"][0]
    assert turn["status"] == "failed"
    assert turn["error"] == "This document is not indexed. Refresh the document list."


def test_document_with_no_matching_passages_says_so(env):
    chat = env.chat()
    env.app.state.chat.retrieve = lambda document, query, limit: []
    turn = events_of(env.ask(chat["id"]))[-1]["conversation"]["turns"][0]
    assert turn["status"] == "completed"
    assert turn["answer"] == "No passages were found in the selected document." and turn["sources"] == []


@pytest.mark.parametrize("error, message", [
    (type("QuotaError", (Exception,), {"code": 429})("quota"), "Gemini quota reached. Wait and retry."),
    (RuntimeError("https://provider?key=secret"),
     "Unable to generate an answer. Check Gemini and local services, then retry."),
])
def test_failures_are_saved_with_safe_messages(env, error, message):
    env.model.error = error
    chat = env.chat()
    events = events_of(env.ask(chat["id"]))
    turn = events[-1]["conversation"]["turns"][0]
    assert turn["status"] == "failed" and turn["error"] == message
    assert "secret" not in json.dumps(events)


def test_retry_after_failure_keeps_question_and_mode(env):
    env.model.error = RuntimeError("boom")
    chat = env.chat()
    turn_id = str(uuid.uuid4())
    env.ask(chat["id"], "More detail", turnId=turn_id, answerMode="detailed")
    env.model.error = None
    body = {"documentId": "a", "turnId": turn_id, "retry": True, "answerMode": "balanced"}
    turns = events_of(env.call("POST", f"/conversations/{chat['id']}/messages", body))[-1]["conversation"]["turns"]
    assert len(turns) == 1 and turns[0]["status"] == "completed"
    assert turns[0]["question"] == "More detail" and turns[0]["answerMode"] == "detailed" and len(turns[0]["sources"]) == 5


@pytest.mark.parametrize("body, status, message", [
    ({"documentId": "a", "question": "q", "turnId": "00000000-0000-4000-8000-000000000000", "retry": "yes"},
     400, "Invalid retry flag."),
    ({"question": "q"}, 400, "Select a document."),
    ({"documentId": "a", "question": "", "turnId": "00000000-0000-4000-8000-000000000000"},
     400, "Enter a question of at most 4,000 characters."),
])
def test_message_validation_returns_json_before_streaming(env, body, status, message):
    chat = env.chat()
    response = env.call("POST", f"/conversations/{chat['id']}/messages", body)
    assert response.status_code == status and response.json() == {"message": message}


def test_duplicate_message_id_is_rejected(env):
    chat = env.chat()
    turn_id = str(uuid.uuid4())
    env.ask(chat["id"], turnId=turn_id)
    response = env.ask(chat["id"], "Again", turnId=turn_id)
    assert response.status_code == 409


# --- Stop, retry and delete while an answer is running ---

def start_held_answer(env, chat_id, turn_id):
    env.model.hold = True
    result = {}
    thread = threading.Thread(target=lambda: result.update(response=env.ask(chat_id, "More detail", turnId=turn_id)))
    thread.start()
    assert env.model.started.wait(5)
    return thread, result


def test_stop_saves_partial_answer_and_retry_completes(env):
    chat = env.chat()
    turn_id = str(uuid.uuid4())
    thread, result = start_held_answer(env, chat["id"], turn_id)

    response = env.call("DELETE", f"/conversations/{chat['id']}?documentId=a")
    assert response.status_code == 409 and response.json() == {"message": "Stop the answer before deleting this chat."}
    assert env.ask(chat["id"], "Another").json() == {"message": "An answer is already being generated in this chat."}
    assert env.call("DELETE", "/documents/a").json() == {"message": "Stop active answers for this document before deleting it."}
    assert env.call("POST", f"/conversations/{chat['id']}/stop", {"documentId": "b"}).status_code == 404
    assert env.call("POST", f"/conversations/{chat['id']}/stop", {"documentId": "a"}).json() == {"message": "Stop requested."}
    thread.join(5)

    events = events_of(result["response"])
    turn = events[-1]["conversation"]["turns"][0]
    assert turn["status"] == "stopped" and turn["error"] == "Answer stopped. You can retry this question."
    assert turn["answer"] == "Synthetic"  # The partial text is kept...
    assert turn["citationNote"].startswith("Incomplete answer. ")
    env.model.release.set()
    time.sleep(0.05)
    saved = env.call("GET", f"/conversations/{chat['id']}?documentId=a").json()["conversation"]["turns"][0]
    assert saved["status"] == "stopped"  # ...and a late result can't overwrite it.

    env.model.hold = False
    retried = events_of(env.ask(chat["id"], turnId=turn_id, retry=True))[-1]["conversation"]["turns"]
    assert len(retried) == 1 and retried[0]["status"] == "completed" and retried[0]["question"] == "More detail"
    assert env.call("DELETE", f"/conversations/{chat['id']}?documentId=a").json() == {"message": "Conversation deleted."}


def test_stopped_partial_answers_are_not_used_as_context(env):
    chat = env.chat()
    thread, _ = start_held_answer(env, chat["id"], str(uuid.uuid4()))
    env.call("POST", f"/conversations/{chat['id']}/stop", {"documentId": "a"})
    thread.join(5)
    env.model.hold = False
    env.model.release.set()
    env.ask(chat["id"], "New question")
    assert env.rewriter.calls == []  # No completed turns yet, so nothing to rewrite with.
    assert env.model.calls[-1][1:] == [("human", "New question")]


def test_new_chats_are_blocked_while_document_is_deleted(env):
    env.app.state.chat.deleting_documents.add("a")
    response = env.call("POST", "/conversations", {"documentId": "a"})
    assert response.status_code == 409 and response.json() == {"message": "This document is being deleted."}


def test_deleting_a_document_removes_its_chats(env):
    chat = env.chat()
    env.ask(chat["id"])
    assert env.call("DELETE", "/documents/a").status_code == 200
    assert env.call("GET", "/conversations?documentId=a").json() == {"conversations": []}


def test_client_disconnect_stops_the_answer():
    import asyncio

    from app.main import stream_events

    async def run():
        queue = asyncio.Queue()
        cancelled = asyncio.Event()

        async def answer():
            await queue.put({"type": "started"})
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(answer())
        stream = stream_events(queue, task, lambda: {})
        assert json.loads(await stream.__anext__()) == {"type": "started"}
        await stream.aclose()  # What the server does when the browser goes away.
        await asyncio.wait_for(cancelled.wait(), 1)

    asyncio.run(run())
