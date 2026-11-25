import json

import httpx
import pytest

from app.ai import DOCUMENT_PREFIX, QUERY_PREFIX, GeminiEmbeddings, GeminiError, QdrantError, QdrantStore


def gemini(responses, calls):
    """An httpx client that returns the given responses in order and records requests."""
    queue = list(responses)

    def handler(request):
        calls.append(request)
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(values):
    return httpx.Response(200, json={"embedding": {"values": values}})


def test_prefixes_match_the_node_version():
    assert QUERY_PREFIX == "task: question answering | query: "
    assert DOCUMENT_PREFIX == "title: none | text: "


def test_embed_document_request_format():
    calls = []
    embeddings = GeminiEmbeddings(api_key="key-123", client=gemini([ok([0.1, 0.2])], calls))
    assert embeddings.embed_document("chunk text") == [0.1, 0.2]
    request = calls[0]
    assert str(request.url) == ("https://generativelanguage.googleapis.com/v1beta/models/"
                                "gemini-embedding-2:embedContent")
    assert request.headers["x-goog-api-key"] == "key-123"
    assert "key-123" not in str(request.url)
    assert json.loads(request.content) == {"content": {"parts": [{"text": "title: none | text: chunk text"}]}}


def test_embed_query_uses_query_prefix():
    calls = []
    GeminiEmbeddings(api_key="k", client=gemini([ok([1.0])], calls)).embed_query("what is it?")
    assert json.loads(calls[0].content)["content"]["parts"][0]["text"] == "task: question answering | query: what is it?"


def test_retries_rate_limits_then_succeeds():
    calls, waits = [], []
    client = gemini([httpx.Response(429), httpx.Response(503), ok([1.0])], calls)
    assert GeminiEmbeddings(api_key="k", client=client, sleep=waits.append).embed_document("x") == [1.0]
    assert len(calls) == 3 and waits == [1, 2]


def test_gives_up_after_two_retries_with_safe_message():
    calls = []
    body = {"error": {"status": "RESOURCE_EXHAUSTED", "message": "details with key-123"}}
    client = gemini([httpx.Response(429, json=body)] * 3, calls)
    with pytest.raises(GeminiError) as raised:
        GeminiEmbeddings(api_key="key-123", client=client, sleep=lambda _s: None).embed_document("x")
    assert raised.value.status == 429
    assert str(raised.value) == "Gemini embedding request failed (429 RESOURCE_EXHAUSTED)"
    assert len(calls) == 3


def test_does_not_retry_permission_errors():
    calls = []
    client = gemini([httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})], calls)
    with pytest.raises(GeminiError, match="403 PERMISSION_DENIED"):
        GeminiEmbeddings(api_key="k", client=client, sleep=lambda _s: None).embed_document("x")
    assert len(calls) == 1


def test_network_errors_are_retried_then_reported():
    calls = []
    client = gemini([httpx.ConnectError("refused")] * 3, calls)
    with pytest.raises(GeminiError, match="could not reach"):
        GeminiEmbeddings(api_key="k", client=client, sleep=lambda _s: None).embed_document("x")
    assert len(calls) == 3


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Set GEMINI_API_KEY"):
        GeminiEmbeddings()


def qdrant(handler):
    return QdrantStore(url="http://qdrant:6333", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_creates_missing_collection_with_cosine_distance():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(404 if request.method == "GET" else 200, json={})

    qdrant(handler).ensure_collection(3072)
    assert calls == [("GET", "/collections/pdf-rag-gemini-embedding-2", None),
                     ("PUT", "/collections/pdf-rag-gemini-embedding-2",
                      {"vectors": {"size": 3072, "distance": "Cosine"}})]


def test_existing_collection_is_left_alone():
    calls = []
    qdrant(lambda request: calls.append(request.method) or httpx.Response(200, json={})).ensure_collection(3)
    assert calls == ["GET"]


def test_upsert_sends_batches_and_waits():
    calls = []

    def handler(request):
        calls.append((request.url.params.get("wait"), len(json.loads(request.content)["points"])))
        return httpx.Response(200, json={})

    points = [{"id": str(index), "vector": [0.0], "payload": {}} for index in range(130)]
    qdrant(handler).upsert(points)
    assert calls == [("true", 64), ("true", 64), ("true", 2)]


def test_qdrant_errors_have_safe_messages():
    with pytest.raises(QdrantError, match="Unable to save document vectors"):
        qdrant(lambda _request: httpx.Response(500)).upsert([{"id": "1"}])
