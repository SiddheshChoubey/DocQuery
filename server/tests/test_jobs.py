import sqlite3

import pytest

from app.jobs import UploadJobs, failure_message


@pytest.fixture
def jobs(tmp_path):
    return UploadJobs(tmp_path / "data" / "conversations.sqlite")


def create(jobs, job_id="job-1", owner="user_a", document="doc-1"):
    return jobs.create(job_id=job_id, owner_id=owner, document_id=document, source_type="pdf",
                       filename="notes.pdf", path="uploads/1-2-notes.pdf", uploaded_at="2026-11-25T10:00:00.000Z")


def test_new_job_waits_without_progress(jobs):
    job = create(jobs)
    assert job["state"] == "waiting"
    assert job["progress"] is None
    assert job["ownerId"] == "user_a" and job["documentId"] == "doc-1"


def test_update_progress_state_and_error(jobs):
    create(jobs)
    jobs.update("job-1", state="active", progress={"phase": "embedding", "completed": 2, "total": 5})
    assert jobs.get("job-1")["progress"] == {"phase": "embedding", "completed": 2, "total": 5}
    jobs.update("job-1", state="failed", error="429 RESOURCE_EXHAUSTED")
    job = jobs.get("job-1")
    assert job["state"] == "failed" and job["error"] == "429 RESOURCE_EXHAUSTED"
    assert job["progress"]["completed"] == 2  # Unchanged fields are kept.


def test_rejects_unknown_state(jobs):
    create(jobs)
    with pytest.raises(ValueError):
        jobs.update("job-1", state="delayed")


def test_remove_and_lookup_by_document(jobs):
    create(jobs, "job-1", "user_a", "doc-1")
    create(jobs, "job-2", "user_b", "doc-1")
    assert [job["id"] for job in jobs.for_document("user_a", "doc-1")] == ["job-1"]
    jobs.remove("job-1")
    assert jobs.get("job-1") is None
    assert jobs.get("missing") is None


def test_shares_database_with_existing_tables(tmp_path):
    path = tmp_path / "conversations.sqlite"
    with sqlite3.connect(path) as db:  # A table the Node server created.
        db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO conversations VALUES ('kept')")
    create(UploadJobs(path))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM conversations").fetchall() == [("kept",)]
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_creating_the_store_does_not_touch_the_disk(tmp_path):
    UploadJobs(tmp_path / "data" / "conversations.sqlite")
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("reason, message", [
    ("Gemini embedding request failed (429 RESOURCE_EXHAUSTED)",
     "Gemini quota was reached while indexing. Wait for your quota to reset, then upload again."),
    ("Your quota is exhausted", "Gemini quota was reached while indexing. Wait for your quota to reset, then upload again."),
    ("Gemini embedding request failed (403 PERMISSION_DENIED)",
     "Gemini rejected the request. Check your API key and model access."),
    ("Set GEMINI_API_KEY in server/.env before starting.",
     "Gemini rejected the request. Check your API key and model access."),
    ("Gemini embedding request failed (404 NOT_FOUND)",
     "A required model or resource was unavailable. Check the worker and Gemini model access."),
    ("No extractable text found. Scanned PDFs need OCR.",
     "This PDF has no extractable text. Scanned PDFs need OCR first."),
    ("Invalid PDF: the file is encrypted.", "This PDF could not be read. Try an unencrypted, text-based PDF."),
    ("Unable to save document vectors to Qdrant.",
     "Indexing failed. Check the PDF worker, Gemini access, and Qdrant, then upload again."),
    ("", "Indexing failed. Check the PDF worker, Gemini access, and Qdrant, then upload again."),
])
def test_failure_messages_match_node(reason, message):
    assert failure_message(reason) == message
