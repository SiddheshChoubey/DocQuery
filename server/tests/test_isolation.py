"""Two signed-in users across every route (isolation.test.js), plus cross-document checks.

Tokens are real RS256 Clerk-style session tokens, verified by the real ClerkAuth.
"""

import json
import threading
import uuid

from tests.conftest import ORIGIN
from tests.test_chat_routes import env, events_of, meta  # noqa: F401  (env is a fixture)


def test_signed_out_and_invalid_tokens_are_rejected_everywhere(env, make_token):  # noqa: F811
    from fastapi.testclient import TestClient

    client = TestClient(env.app)
    routes = [("GET", "/documents"), ("DELETE", "/documents/a"), ("POST", "/upload/pdf"), ("POST", "/upload/text"),
              ("GET", "/upload/status/unknown"), ("GET", "/conversations?documentId=a"), ("POST", "/conversations"),
              ("GET", "/conversations/x?documentId=a"), ("DELETE", "/conversations/x?documentId=a"),
              ("POST", "/conversations/x/stop"), ("POST", "/conversations/x/messages")]
    bad_tokens = ["bad.token", make_token(exp=1), make_token(iss="https://another-instance.test"),
                  make_token(azp="https://evil.test"), make_token(sid=None), make_token(azp=None)]
    for method, url in routes:
        assert client.request(method, url, headers={"Origin": ORIGIN}).status_code == 401, url
        for token in bad_tokens:
            response = client.request(method, url, headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"})
            assert response.status_code == 401, (url, token)


def test_two_users_cannot_reach_each_others_data(env, tmp_path):  # noqa: F811
    # Each user sees only their own documents.
    assert [doc["id"] for doc in env.call("GET", "/documents").json()["documents"]] == ["a", "b"]
    assert [doc["id"] for doc in env.call("GET", "/documents", owner="user_b").json()["documents"]] == ["c"]
    assert env.call("GET", "/documents", owner="user_empty").json() == {"documents": []}

    # Uploads always belong to the signed-in user, whatever the body says.
    uploaded = env.call("POST", "/upload/text", {"title": "Note", "text": "Synthetic", "ownerId": "user_b"}).json()
    assert env.call("GET", f"/upload/status/{uploaded['jobId']}").status_code == 200
    assert env.call("GET", f"/upload/status/{uploaded['jobId']}", owner="user_b").status_code == 404

    # user_b can't delete, chat with, or open chats on user_a's document.
    assert env.call("DELETE", "/documents/a", owner="user_b").status_code == 404
    assert any(point["payload"]["metadata"]["documentId"] == "a" for point in env.qdrant.points)
    assert env.call("POST", "/conversations", {"documentId": "a"}, owner="user_b").status_code == 404
    created = env.call("POST", "/conversations", {"documentId": "a", "ownerId": "user_b"}).json()["conversation"]
    assert created["ownerId"] == "user_a"
    chat_id = created["id"]
    assert env.call("GET", "/conversations?documentId=a", owner="user_b").json() == {"conversations": []}
    assert env.call("GET", f"/conversations/{chat_id}?documentId=a", owner="user_b").status_code == 404
    for retry in (False, True):
        assert env.ask(chat_id, "Intrusion", owner="user_b", retry=retry).status_code == 404

    # user_a's own chat works; while it's answering, user_b can't stop or delete it.
    assert events_of(env.ask(chat_id))[-1]["conversation"]["turns"][0]["status"] == "completed"
    env.model.hold = True
    result = {}
    turn_id = str(uuid.uuid4())
    thread = threading.Thread(target=lambda: result.update(response=env.ask(chat_id, "Next", turnId=turn_id)))
    thread.start()
    assert env.model.started.wait(5)
    assert env.call("POST", f"/conversations/{chat_id}/stop", {"documentId": "a"}, owner="user_b").status_code == 404
    assert env.call("DELETE", f"/conversations/{chat_id}?documentId=a", owner="user_b").status_code == 404
    assert chat_id in env.app.state.chat.active
    assert env.call("POST", f"/conversations/{chat_id}/stop", {"documentId": "a"}).status_code == 200
    thread.join(5)
    env.model.release.set()
    assert events_of(result["response"])[-1]["conversation"]["turns"][1]["status"] == "stopped"
    env.model.hold = False
    retried = events_of(env.ask(chat_id, turnId=turn_id, retry=True))[-1]["conversation"]["turns"][1]
    assert retried["status"] == "completed"

    # Deleting a document removes only its owner's chats.
    from app.conversations import ConversationStore

    store = ConversationStore(env.database)
    other_users_chat = store.create("a", "user_b")
    assert env.call("DELETE", "/documents/a").status_code == 200
    assert store.get(other_users_chat["id"], "a", "user_b")["id"] == other_users_chat["id"]
    assert env.call("GET", "/conversations?documentId=a").json() == {"conversations": []}


def test_answers_never_use_another_users_passages(env):  # noqa: F811
    env.qdrant.add(meta("a", owner="user_b"), content="user_b's chunk with the same document id")
    chat = env.chat()
    turn = events_of(env.ask(chat["id"], answerMode="detailed"))[-1]["conversation"]["turns"][0]
    assert all("user_b" not in source["passage"] for source in turn["sources"])
    query = [body for path, body in env.qdrant.requests if path.endswith("/points/query")][-1]
    assert {"key": "metadata.ownerId", "match": {"value": "user_a"}} in query["filter"]["must"]


def test_follow_up_history_stays_within_its_document(env):  # noqa: F811
    chat_a, chat_b = env.chat("a"), env.chat("b")
    env.ask(chat_a["id"], "Question about A")
    env.ask(chat_b["id"], "First question about B", document="b")
    assert env.rewriter.calls == []  # Neither chat had history yet.
    env.ask(chat_b["id"], "And then?", document="b")
    sent = json.loads(env.rewriter.calls[-1][1][1])
    assert [message["content"] for message in sent["history"]] == ["First question about B", "Synthetic answer [1]."]
    assert "Question about A" not in json.dumps(env.model.calls[-1])


def test_pasted_text_is_cited_without_page_numbers(env):  # noqa: F811
    env.qdrant.add({**meta("note", sourceType="text", fileName="My note"), "source": None, "loc": None},
                   content="Pasted text passage")
    env.qdrant.points[-1]["payload"]["metadata"] = {"documentId": "note", "ownerId": "user_a", "fileName": "My note",
                                                   "sourceType": "text", "uploadedAt": "2026-11-25T10:00:00.000Z"}
    chat = env.chat("note")
    turn = events_of(env.ask(chat["id"], document="note"))[-1]["conversation"]["turns"][0]
    assert turn["sources"] == [{"id": 1, "filename": "My note", "sourceType": "text", "page": None,
                                "passage": "Pasted text passage", "cited": True}]
