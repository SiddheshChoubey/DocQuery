"""A tiny in-memory stand-in for Qdrant's REST API, used through the real QdrantStore."""

import json

import httpx

from app.ai import QdrantStore


def _value(payload: dict, key: str):
    for part in key.split("."):
        payload = payload.get(part) if isinstance(payload, dict) else None
    return payload


def _matches(payload: dict, filter: dict | None) -> bool:
    return all(_value(payload, condition["key"]) == condition["match"]["value"]
               for condition in (filter or {}).get("must", []))


class FakeQdrant:
    def __init__(self, page_size: int = 2):
        self.points: list[dict] = []  # {"id", "payload"}
        self.exists = True
        self.page_size = page_size  # Small pages so tests exercise pagination.
        self.requests: list[tuple[str, dict]] = []
        self.fail_delete = False
        self.fail_scroll = False

    def add(self, metadata: dict, content: str = "chunk") -> None:
        self.points.append({"id": str(len(self.points)), "payload": {"content": content, "metadata": metadata}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if not self.exists:
            return httpx.Response(404, json={})
        if request.url.path.endswith("/points/scroll"):
            if self.fail_scroll:
                return httpx.Response(500)
            matching = [point for point in self.points if _matches(point["payload"], body.get("filter"))]
            start = int(body.get("offset") or 0)
            page = matching[start:start + self.page_size]
            next_offset = start + self.page_size if start + self.page_size < len(matching) else None
            return httpx.Response(200, json={"result": {"points": [
                {"id": point["id"], "payload": {"metadata": point["payload"]["metadata"]}} for point in page],
                "next_page_offset": next_offset}})
        if request.url.path.endswith("/points/query"):
            matching = [point for point in self.points if _matches(point["payload"], body.get("filter"))]
            return httpx.Response(200, json={"result": {"points": [
                {"id": point["id"], "score": 1.0, "payload": point["payload"]} for point in matching[:body["limit"]]]}})
        if request.url.path.endswith("/points/delete"):
            if self.fail_delete:
                return httpx.Response(500)
            self.points = [point for point in self.points if not _matches(point["payload"], body["filter"])]
            return httpx.Response(200, json={})
        return httpx.Response(400)

    def store(self) -> QdrantStore:
        return QdrantStore(url="http://qdrant:6333", client=httpx.Client(transport=httpx.MockTransport(self.handler)))
