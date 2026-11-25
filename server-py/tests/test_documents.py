import os
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.conversations import ConversationStore
from app.documents import (document_from_metadata, legacy_document_id, list_documents, stored_pdf_path)
from app.errors import ApiError
from app.jobs import UploadJobs
from app.main import create_app
from tests.conftest import ORIGIN
from tests.fake_qdrant import FakeQdrant


def meta(document_id, owner="user_a", filename="notes.pdf", **extra):
    return {"documentId": document_id, "ownerId": owner, "fileName": filename, "sourceType": "pdf",
            "uploadedAt": "2026-11-25T10:00:00.000Z", **extra}


# --- Metadata → document (documents.test.js) ---

def test_same_filenames_from_separate_uploads_stay_separate():
    first, second = document_from_metadata(meta("a")), document_from_metadata(meta("b"))
    assert first["id"] != second["id"]
    assert first["filter"] == {"must": [{"key": "metadata.documentId", "match": {"value": "a"}},
                                        {"key": "metadata.ownerId", "match": {"value": "user_a"}}]}


def test_legacy_documents_use_their_exact_path():
    source = "uploads/1792274133998-123-mead.pdf"
    document = document_from_metadata({"source": source})
    assert document["filename"] == "mead.pdf"
    assert document["id"] == legacy_document_id(source)
    assert document["id"].startswith("legacy-") and len(document["id"]) == 7 + 64
    assert document["filter"] == {"must": [{"key": "metadata.source", "match": {"value": source}}]}
    assert document_from_metadata({"source": "uploads/1792274181870-662800962-mead.pdf"})["id"] != document["id"]
    assert document_from_metadata({}) is None
    assert document_from_metadata(None) is None


def test_legacy_id_matches_node():
    # Value produced by Node: createHash('sha256').update('uploads/a.pdf').digest('hex')
    assert legacy_document_id("uploads/a.pdf") == \
        "legacy-9ed57f30c0f8cbbc1818ccdf0d32bd71809b00c73d6eb94b0bc1b55651e2bd21"


def test_windows_style_legacy_paths():
    document = document_from_metadata({"source": "uploads\\1792274133998-123-notes.pdf"})
    assert document["filename"] == "notes.pdf"


def test_upload_dates():
    assert document_from_metadata(meta("a", uploadedAt="2026-10-18T10:00:00Z"))["uploadedAt"] == "2026-10-18T10:00:00.000Z"
    legacy = document_from_metadata({"source": "uploads/1792274133998-123-notes.pdf"})["uploadedAt"]
    assert legacy == datetime.fromtimestamp(1792274133.998, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert document_from_metadata({"documentId": "a"})["uploadedAt"] is None
    assert document_from_metadata(meta("a", uploadedAt="invalid"))["uploadedAt"] is None


def test_text_documents_and_default_filename():
    assert document_from_metadata(meta("a", sourceType="text"))["sourceType"] == "text"
    assert document_from_metadata({"documentId": "a"})["filename"] == "PDF"


# --- Listing ---

def test_listing_reads_every_page_and_counts_chunks():
    qdrant = FakeQdrant(page_size=2)
    for _ in range(3):
        qdrant.add(meta("a", filename="Beta.pdf", source="uploads/1-2-Beta.pdf"))
    qdrant.add(meta("b", filename="alpha.pdf"))
    documents = list_documents(qdrant.store(), "user_a")
    assert [(doc["id"], doc["chunkCount"]) for doc in documents] == [("b", 1), ("a", 3)]  # Sorted by name.
    assert documents[1]["sourcePaths"] == ["uploads/1-2-Beta.pdf"]
    scrolls = [body for path, body in qdrant.requests if path.endswith("/scroll")]
    assert len(scrolls) == 2 and scrolls[1]["offset"] == 2
    assert all(body["with_vector"] is False for body in scrolls)
    assert scrolls[0]["with_payload"] == ["metadata.documentId", "metadata.fileName", "metadata.source",
                                          "metadata.sourceType", "metadata.uploadedAt", "metadata.ownerId"]
    assert scrolls[0]["filter"] == {"must": [{"key": "metadata.ownerId", "match": {"value": "user_a"}}]}


def test_listing_is_per_owner():
    qdrant = FakeQdrant()
    qdrant.add(meta("a", owner="user_a"))
    qdrant.add(meta("b", owner="user_b"))
    assert [doc["id"] for doc in list_documents(qdrant.store(), "user_a")] == ["a"]
    assert [doc["id"] for doc in list_documents(qdrant.store(), "user_b")] == ["b"]
    assert list_documents(qdrant.store(), "user_empty") == []
    assert len(list_documents(qdrant.store())) == 2


def test_empty_collection_is_empty_list_but_failures_are_errors():
    qdrant = FakeQdrant()
    qdrant.exists = False
    assert list_documents(qdrant.store(), "user_a") == []
    qdrant.exists, qdrant.fail_scroll = True, True
    with pytest.raises(Exception, match="Unable to list"):
        list_documents(qdrant.store(), "user_a")


# --- Stored file safety (delete-document.test.js) ---

def test_stored_pdf_path_safety(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.pdf").write_bytes(b"pdf")
    (tmp_path / "outside.txt").write_text("keep this file")
    (uploads / "folder").mkdir()
    assert stored_pdf_path("uploads/a.pdf", tmp_path) == uploads / "a.pdf"
    assert stored_pdf_path("uploads\\a.pdf", tmp_path) == uploads / "a.pdf"
    assert stored_pdf_path("uploads/missing.pdf", tmp_path) is None
    for source in ["outside.txt", "uploads/../outside.txt", "uploads", "uploads/folder"]:
        with pytest.raises(ApiError) as raised:
            stored_pdf_path(source, tmp_path)
        assert raised.value.status == 409
    assert (tmp_path / "outside.txt").read_text() == "keep this file"


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs admin rights on Windows")
def test_stored_pdf_path_refuses_links(tmp_path):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "outside.txt").write_text("keep")
    (tmp_path / "uploads" / "link.pdf").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ApiError):
        stored_pdf_path("uploads/link.pdf", tmp_path)
    assert (tmp_path / "outside.txt").read_text() == "keep"


# --- Routes ---

@pytest.fixture
def setup(tmp_path, auth, make_token):
    database = tmp_path / "data" / "conversations.sqlite"
    jobs, conversations, qdrant = UploadJobs(database), ConversationStore(database), FakeQdrant()
    (tmp_path / "uploads").mkdir()
    app = create_app(auth=auth, jobs=jobs, enqueue=lambda *_args: None, storage_root=tmp_path,
                     store=qdrant.store(), conversations=conversations)

    def headers(owner="user_a"):
        return {"Authorization": f"Bearer {make_token(owner)}", "Origin": ORIGIN}

    return type("Setup", (), {"client": TestClient(app, raise_server_exceptions=False), "app": app,
                              "jobs": jobs, "qdrant": qdrant, "root": tmp_path, "database": database,
                              "headers": staticmethod(headers)})


def add_pdf(setup, document_id, owner="user_a", job_state="completed", filename="notes.pdf"):
    path = f"uploads/1-{document_id}-{filename}"
    (setup.root / path).write_bytes(b"pdf")
    setup.qdrant.add(meta(document_id, owner, filename, source=path))
    setup.qdrant.add(meta(document_id, owner, filename, source=path))
    setup.jobs.create(job_id=f"job-{document_id}", owner_id=owner, document_id=document_id, source_type="pdf",
                      filename=filename, path=path, uploaded_at="2026-11-25T10:00:00.000Z")
    setup.jobs.update(f"job-{document_id}", state=job_state)
    return setup.root / path


def add_conversation(setup, document_id, owner="user_a"):
    with sqlite3.connect(setup.database) as db:
        ConversationStore(setup.database)._connect().close()
        db.execute("INSERT INTO conversations (id, documentId, title, createdAt, updatedAt, ownerId)"
                   " VALUES (?, ?, 'Chat', 'now', 'now', ?)", (f"chat-{document_id}-{owner}", document_id, owner))
        db.execute("INSERT INTO turns (id, conversationId, question, createdAt, status)"
                   " VALUES (?, ?, 'q', 'now', 'completed')", (f"turn-{document_id}-{owner}", f"chat-{document_id}-{owner}"))


def count(database, table):
    with sqlite3.connect(database) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_get_documents_returns_only_public_fields(setup):
    add_pdf(setup, "a")
    response = setup.client.get("/documents", headers=setup.headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"documents": [{"id": "a", "filename": "notes.pdf", "chunkCount": 2,
                                              "sourceType": "pdf", "uploadedAt": "2026-11-25T10:00:00.000Z"}]}


def test_get_documents_needs_sign_in(setup):
    assert setup.client.get("/documents").status_code == 401


def test_delete_removes_file_job_vectors_and_chats(setup):
    file = add_pdf(setup, "a")
    keep = add_pdf(setup, "b")
    add_conversation(setup, "a")
    add_conversation(setup, "b")
    response = setup.client.delete("/documents/a", headers=setup.headers())
    assert response.status_code == 200
    assert response.json() == {"message": "Document deleted.", "documentId": "a"}
    assert not file.exists() and keep.exists()
    assert setup.jobs.get("job-a") is None and setup.jobs.get("job-b") is not None
    assert {point["payload"]["metadata"]["documentId"] for point in setup.qdrant.points} == {"b"}
    assert count(setup.database, "conversations") == 1
    assert count(setup.database, "turns") == 1  # The deleted chat's turns went with it.
    delete_request = [body for path, body in setup.qdrant.requests if path.endswith("/points/delete")][0]
    assert delete_request == {"filter": {"must": [{"key": "metadata.documentId", "match": {"value": "a"}},
                                                  {"key": "metadata.ownerId", "match": {"value": "user_a"}}]}}


def test_cannot_delete_another_users_document(setup):
    file = add_pdf(setup, "a", owner="user_a")
    response = setup.client.delete("/documents/a", headers=setup.headers("user_b"))
    assert response.status_code == 404
    assert response.json() == {"message": "Document not found. Refresh the document list."}
    assert file.exists() and len(setup.qdrant.points) == 2


@pytest.mark.parametrize("state", ["waiting", "active"])
def test_indexing_blocks_deletion_before_any_change(setup, state):
    file = add_pdf(setup, "a", job_state=state)
    response = setup.client.delete("/documents/a", headers=setup.headers())
    assert response.status_code == 409
    assert response.json() == {"message": "This document is still indexing. Wait until indexing finishes before deleting it."}
    assert file.exists() and setup.jobs.get("job-a") is not None and len(setup.qdrant.points) == 2


def test_failed_job_does_not_block_deletion(setup):
    add_pdf(setup, "a", job_state="failed")
    assert setup.client.delete("/documents/a", headers=setup.headers()).status_code == 200


def test_shared_file_blocks_deletion(setup):
    file = add_pdf(setup, "a")
    setup.qdrant.add(meta("other", "user_b", source="uploads/1-a-notes.pdf"))  # Another user's doc, same file.
    response = setup.client.delete("/documents/a", headers=setup.headers())
    assert response.status_code == 409
    assert response.json() == {"message": "Another document shares this stored file. Check the uploads before deleting it."}
    assert file.exists()


def test_document_being_deleted_cannot_be_deleted_twice(setup):
    add_pdf(setup, "a")
    setup.app.state.chat.deleting_documents.add("a")
    response = setup.client.delete("/documents/a", headers=setup.headers())
    assert response.status_code == 409
    assert response.json() == {"message": "This document is being deleted."}


def test_missing_file_does_not_block_deletion(setup):
    add_pdf(setup, "a").unlink()
    assert setup.client.delete("/documents/a", headers=setup.headers()).status_code == 200
    assert setup.qdrant.points == []


def test_qdrant_failure_keeps_document_listed_for_retry(setup):
    add_pdf(setup, "a")
    setup.qdrant.fail_delete = True
    response = setup.client.delete("/documents/a", headers=setup.headers())
    assert response.status_code == 500
    assert response.json() == {"message": "Request failed. Check the API key, model access, Qdrant, and Valkey."}
    assert "a" not in setup.app.state.chat.deleting_documents  # A retry is allowed.
    setup.qdrant.fail_delete = False
    assert [doc["id"] for doc in setup.client.get("/documents", headers=setup.headers()).json()["documents"]] == ["a"]
    assert setup.client.delete("/documents/a", headers=setup.headers()).status_code == 200


def test_text_document_deletion(setup):
    setup.qdrant.add(meta("t", sourceType="text", filename="My note"))
    setup.jobs.create(job_id="job-t", owner_id="user_a", document_id="t", source_type="text", filename="My note",
                      path=None, uploaded_at="2026-11-25T10:00:00.000Z")
    setup.jobs.update("job-t", state="completed")
    assert setup.client.delete("/documents/t", headers=setup.headers()).status_code == 200
    assert setup.qdrant.points == [] and setup.jobs.get("job-t") is None


def test_conversation_schema_matches_node(tmp_path):
    database = tmp_path / "conversations.sqlite"
    ConversationStore(database).remove_document("x", "user_a")
    with sqlite3.connect(database) as db:
        turn_columns = [row[1] for row in db.execute("PRAGMA table_info(turns)")]
        conversation_columns = [row[1] for row in db.execute("PRAGMA table_info(conversations)")]
    assert turn_columns == ["id", "conversationId", "question", "answer", "sources", "citationNote", "error",
                            "createdAt", "status", "answerMode"]
    assert conversation_columns == ["id", "documentId", "title", "createdAt", "updatedAt", "ownerId"]
