import json

import pytest
from fastapi.testclient import TestClient

from app.jobs import UploadJobs
from app.main import create_app
from tests.conftest import ORIGIN


class FakeQueue:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, job_id, text=None):
        if self.fail:
            raise ConnectionError("Error 111 connecting to localhost:6379")
        self.sent.append((job_id, text))


@pytest.fixture
def setup(tmp_path, auth, make_token):
    jobs = UploadJobs(tmp_path / "data" / "conversations.sqlite")
    queue = FakeQueue()
    uploads = tmp_path / "uploads"
    app = create_app(auth=auth, jobs=jobs, enqueue=queue, storage_root=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    def headers(owner="user_a", **extra):
        return {"Authorization": f"Bearer {make_token(owner)}", "Origin": ORIGIN, **extra}

    return type("Setup", (), {"client": client, "jobs": jobs, "queue": queue, "uploads": uploads,
                              "headers": staticmethod(headers), "app": app})


# --- PDF uploads ---

def test_pdf_upload_saves_file_and_queues_job(setup):
    response = setup.client.post("/upload/pdf", headers=setup.headers(),
                                 files={"pdf": ("notes.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert response.status_code == 202
    data = response.json()
    assert data["message"] == "PDF uploaded; queued for indexing."
    job = setup.jobs.get(data["jobId"])
    assert job["state"] == "waiting" and job["ownerId"] == "user_a"
    assert job["documentId"] == data["documentId"] and job["filename"] == "notes.pdf"
    assert job["sourceType"] == "pdf"
    assert job["path"].startswith("uploads/") and job["path"].endswith("-notes.pdf")
    assert (setup.uploads.parent / job["path"]).read_bytes() == b"%PDF-1.4 test"
    assert setup.queue.sent == [(data["jobId"], None)]
    assert job["uploadedAt"].endswith("Z")


def test_pdf_upload_requires_a_file(setup):
    for kwargs in [{"files": {"other": ("a.pdf", b"x")}}, {"data": {"pdf": "not a file"}}, {"json": {}}, {}]:
        response = setup.client.post("/upload/pdf", headers=setup.headers(), **kwargs)
        assert response.status_code == 400
        assert response.json() == {"message": "Select a PDF to upload."}
    assert setup.queue.sent == []


def test_pdf_filename_cannot_escape_uploads(setup):
    response = setup.client.post("/upload/pdf", headers=setup.headers(),
                                 files={"pdf": ("..\\..\\evil.pdf", b"x", "application/pdf")})
    job = setup.jobs.get(response.json()["jobId"])
    assert job["path"].startswith("uploads/") and "/" not in job["path"][len("uploads/"):]
    assert job["path"].endswith("-evil.pdf")
    assert list(setup.uploads.iterdir())[0].name.endswith("-evil.pdf")


def test_pdf_upload_needs_sign_in(setup):
    response = setup.client.post("/upload/pdf", files={"pdf": ("notes.pdf", b"x")})
    assert response.status_code == 401
    assert not setup.uploads.exists()


# --- Text uploads ---

def test_text_upload_queues_trimmed_text(setup):
    response = setup.client.post("/upload/text", headers=setup.headers(),
                                 json={"title": "  My note ", "text": "  Some text  ", "ownerId": "user_b"})
    assert response.status_code == 202
    data = response.json()
    assert data["message"] == "Text queued for indexing."
    job = setup.jobs.get(data["jobId"])
    assert job["ownerId"] == "user_a"  # The body cannot choose the owner.
    assert job["filename"] == "My note" and job["sourceType"] == "text" and job["path"] is None
    assert setup.queue.sent == [(data["jobId"], "Some text")]


@pytest.mark.parametrize("body, message", [
    ({}, "Enter a title."),
    ({"title": "   ", "text": "x"}, "Enter a title."),
    ({"title": 5, "text": "x"}, "Enter a title."),
    ({"title": "t" * 121, "text": "x"}, "Keep the title within 120 characters."),
    ({"title": "Note"}, "Paste some text to index."),
    ({"title": "Note", "text": "  "}, "Paste some text to index."),
    ({"title": "Note", "text": "x" * 50001}, "Keep pasted text within 50,000 characters."),
    ([], "Enter a title."),
])
def test_text_validation_messages(setup, body, message):
    response = setup.client.post("/upload/text", headers=setup.headers(), json=body)
    assert response.status_code == 400
    assert response.json() == {"message": message}
    assert setup.queue.sent == []


def test_title_of_exactly_120_characters_is_allowed(setup):
    response = setup.client.post("/upload/text", headers=setup.headers(), json={"title": "t" * 120, "text": "x"})
    assert response.status_code == 202


def test_text_body_over_512kb_is_rejected(setup):
    body = json.dumps({"title": "Big", "text": "x" * (600 * 1024)})
    response = setup.client.post("/upload/text", content=body,
                                 headers=setup.headers(**{"Content-Type": "application/json"}))
    assert response.status_code == 413
    assert response.json() == {"message": "Pasted text is too large. Use at most 50,000 characters."}


@pytest.mark.parametrize("raw", ['{"title": ', '"just a string"', "42"])
def test_invalid_json_is_rejected(setup, raw):
    response = setup.client.post("/upload/text", content=raw,
                                 headers=setup.headers(**{"Content-Type": "application/json"}))
    assert response.status_code == 400
    assert response.json() == {"message": "Send valid JSON in the request body."}


def test_non_json_body_is_treated_as_empty(setup):
    response = setup.client.post("/upload/text", content="title=x",
                                 headers=setup.headers(**{"Content-Type": "text/plain"}))
    assert response.json() == {"message": "Enter a title."}


def test_queue_failure_removes_the_job(setup):
    setup.queue.fail = True
    response = setup.client.post("/upload/text", headers=setup.headers(), json={"title": "Note", "text": "x"})
    assert response.status_code == 500
    assert response.json() == {"message": "Request failed. Check the API key, model access, Qdrant, and Valkey."}
    with setup.jobs._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0] == 0


# --- Status ---

def queue_text(setup, owner="user_a"):
    return setup.client.post("/upload/text", headers=setup.headers(owner),
                             json={"title": "Note", "text": "x"}).json()


def test_status_of_waiting_job(setup):
    queued = queue_text(setup)
    response = setup.client.get(f"/upload/status/{queued['jobId']}", headers=setup.headers())
    assert response.status_code == 200
    assert response.json() == {"state": "waiting", "documentId": queued["documentId"], "progress": 0}
    assert response.headers["cache-control"] == "no-store"


def test_status_of_active_and_completed_jobs(setup):
    queued = queue_text(setup)
    progress = {"phase": "embedding", "completed": 3, "total": 7}
    setup.jobs.update(queued["jobId"], state="active", progress=progress)
    data = setup.client.get(f"/upload/status/{queued['jobId']}", headers=setup.headers()).json()
    assert data == {"state": "active", "documentId": queued["documentId"], "progress": progress}
    setup.jobs.update(queued["jobId"], state="completed", progress={"phase": "ready", "completed": 7, "total": 7})
    assert setup.client.get(f"/upload/status/{queued['jobId']}", headers=setup.headers()).json()["state"] == "completed"


def test_status_of_failed_job_shows_safe_message(setup):
    queued = queue_text(setup)
    setup.jobs.update(queued["jobId"], state="failed", error="Gemini embedding request failed (429 RESOURCE_EXHAUSTED)")
    data = setup.client.get(f"/upload/status/{queued['jobId']}", headers=setup.headers()).json()
    assert data["state"] == "failed"
    assert data["message"] == "Gemini quota was reached while indexing. Wait for your quota to reset, then upload again."
    assert "RESOURCE_EXHAUSTED" not in json.dumps(data)


def test_status_of_unknown_job(setup):
    response = setup.client.get("/upload/status/missing", headers=setup.headers())
    assert response.status_code == 404
    assert response.json() == {"message": "Upload job not found. Submit the document again."}


def test_status_hides_other_users_jobs(setup):
    queued = queue_text(setup, owner="user_a")
    response = setup.client.get(f"/upload/status/{queued['jobId']}", headers=setup.headers("user_b"))
    assert response.status_code == 404
    assert response.json() == {"message": "Upload job not found."}
