import pytest

pytest.importorskip("langchain_text_splitters", reason="install requirements.txt to run ingestion tests")

from app.ingestion import NO_TEXT_ERROR, point_id, prepare_documents, run_indexing  # noqa: E402
from app.jobs import UploadJobs  # noqa: E402
from tests.conftest import build_pdf  # noqa: E402


def make_job(jobs, source_type="pdf", path="uploads/1-2-notes.pdf", owner="user_a"):
    return jobs.create(job_id="job-1", owner_id=owner, document_id="doc-1", source_type=source_type,
                       filename="notes.pdf" if source_type == "pdf" else "My note", path=path,
                       uploaded_at="2026-11-25T10:00:00.000Z")


@pytest.fixture
def storage(tmp_path):
    (tmp_path / "uploads").mkdir()
    return tmp_path


@pytest.fixture
def jobs(tmp_path):
    return UploadJobs(tmp_path / "data" / "conversations.sqlite")


class FakeEmbeddings:
    def __init__(self, jobs=None):
        self.texts, self.jobs, self.progress_seen = [], jobs, []

    def embed_document(self, text):
        self.texts.append(text)
        if self.jobs:
            self.progress_seen.append(self.jobs.get("job-1")["progress"])
        return [float(len(self.texts)), 0.5]


class FakeStore:
    def __init__(self, fail=False):
        self.size, self.points, self.fail = None, [], fail

    def ensure_collection(self, size):
        self.size = size

    def upsert(self, points):
        if self.fail:
            raise RuntimeError("Unable to save document vectors to Qdrant.")
        self.points.extend(points)


def index(jobs, storage, text=None, embeddings=None, store=None):
    embeddings, store = embeddings or FakeEmbeddings(jobs), store or FakeStore()
    run_indexing("job-1", text, jobs=jobs, make_embeddings=lambda: embeddings,
                 make_store=lambda: store, storage_root=storage)
    return embeddings, store


def test_pdf_pages_are_numbered_from_one_like_node(jobs, storage):
    (storage / "uploads" / "1-2-notes.pdf").write_bytes(build_pdf(["First page text", "", "Third page text"]))
    chunks = prepare_documents(make_job(jobs), None, storage)
    assert [chunk.page_content for chunk in chunks] == ["First page text", "Third page text"]
    assert chunks[0].metadata == {"source": "uploads/1-2-notes.pdf", "loc": {"pageNumber": 1},
                                  "ownerId": "user_a", "documentId": "doc-1", "fileName": "notes.pdf",
                                  "sourceType": "pdf", "uploadedAt": "2026-11-25T10:00:00.000Z"}
    assert chunks[1].metadata["loc"] == {"pageNumber": 3}  # The empty page 2 is dropped, numbering kept.


def test_text_is_split_with_node_settings(jobs, storage):
    text = " ".join(f"word{index}" for index in range(1000))  # About 6,900 characters.
    chunks = prepare_documents(make_job(jobs, "text", None), text, storage)
    assert len(chunks) > 4
    assert all(len(chunk.page_content) <= 1500 for chunk in chunks)
    assert chunks[0].page_content[-100:] in text and chunks[1].page_content[:50] in chunks[0].page_content  # Overlap.
    assert chunks[0].metadata == {"ownerId": "user_a", "documentId": "doc-1", "fileName": "My note",
                                  "sourceType": "text", "uploadedAt": "2026-11-25T10:00:00.000Z"}


def test_successful_indexing_reports_progress_and_stores_points(jobs, storage):
    (storage / "uploads" / "1-2-notes.pdf").write_bytes(build_pdf(["Alpha", "Beta"]))
    make_job(jobs)
    embeddings, store = index(jobs, storage)
    assert embeddings.texts == ["Alpha", "Beta"]  # The prefix is added inside GeminiEmbeddings.
    assert embeddings.progress_seen == [{"phase": "embedding", "completed": 0, "total": 2},
                                        {"phase": "embedding", "completed": 1, "total": 2}]
    job = jobs.get("job-1")
    assert job["state"] == "completed"
    assert job["progress"] == {"phase": "ready", "completed": 2, "total": 2}
    assert store.size == 2
    assert [point["payload"]["content"] for point in store.points] == ["Alpha", "Beta"]
    assert store.points[1]["payload"]["metadata"]["loc"] == {"pageNumber": 2}
    assert [point["id"] for point in store.points] == [point_id("doc-1", 0), point_id("doc-1", 1)]


def test_point_ids_are_stable_so_retries_do_not_duplicate():
    assert point_id("doc-1", 0) == point_id("doc-1", 0)
    assert point_id("doc-1", 0) != point_id("doc-1", 1) != point_id("doc-2", 1)


def test_text_job(jobs, storage):
    make_job(jobs, "text", None)
    embeddings, store = index(jobs, storage, text="Pasted note")
    assert embeddings.texts == ["Pasted note"]
    assert jobs.get("job-1")["state"] == "completed"
    assert "source" not in store.points[0]["payload"]["metadata"]


def test_pdf_without_text_fails_with_ocr_message(jobs, storage):
    (storage / "uploads" / "1-2-notes.pdf").write_bytes(build_pdf(["", ""]))
    make_job(jobs)
    _, store = index(jobs, storage)
    job = jobs.get("job-1")
    assert job["state"] == "failed" and job["error"] == NO_TEXT_ERROR
    assert store.points == []


def test_unreadable_pdf_fails_with_invalid_pdf(jobs, storage):
    (storage / "uploads" / "1-2-notes.pdf").write_bytes(b"this is not a pdf")
    make_job(jobs)
    index(jobs, storage)
    assert jobs.get("job-1")["error"].startswith("Invalid PDF")


def test_storage_errors_mark_the_job_failed(jobs, storage):
    make_job(jobs, "text", None)
    index(jobs, storage, text="note", store=FakeStore(fail=True))
    assert jobs.get("job-1")["state"] == "failed"
    assert jobs.get("job-1")["progress"]["phase"] == "saving"


def test_job_without_owner_is_refused(jobs, storage):
    make_job(jobs, "text", None, owner="anonymous")
    embeddings, _ = index(jobs, storage, text="note")
    assert jobs.get("job-1")["state"] == "failed" and embeddings.texts == []


def test_finished_or_deleted_jobs_are_skipped(jobs, storage):
    make_job(jobs, "text", None)
    jobs.update("job-1", state="completed")
    embeddings, _ = index(jobs, storage, text="note")
    assert embeddings.texts == []
    jobs.remove("job-1")
    embeddings, _ = index(jobs, storage, text="note")
    assert embeddings.texts == []


def test_interrupted_job_runs_again(jobs, storage):
    make_job(jobs, "text", None)
    jobs.update("job-1", state="active", progress={"phase": "embedding", "completed": 1, "total": 3})
    index(jobs, storage, text="note")
    assert jobs.get("job-1")["state"] == "completed"
