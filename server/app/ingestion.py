"""Reading, chunking, embedding, and storing an uploaded document.

`run_indexing` is the work the Celery task performs. It takes its services as
arguments, so tests can run it without Celery, Gemini, or Qdrant.
"""

import logging
import uuid
from pathlib import Path
from typing import Callable, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.jobs import UploadJobs, failure_message

logger = logging.getLogger("docquery.worker")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
NO_TEXT_ERROR = "No extractable text found. Scanned PDFs need OCR."


def load_pages(job: dict, text: Optional[str], storage_root: Path) -> list[Document]:
    if job["sourceType"] == "text":
        return [Document(page_content=text or "", metadata={})]
    try:
        reader = PdfReader(storage_root / job["path"])
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("Invalid PDF: the file is encrypted.")
        # Same metadata shape as LangChain JS's PDFLoader: `source` is the stored path
        # (used to delete the file) and pages are numbered from 1 (used in citations).
        return [Document(page_content=page.extract_text() or "",
                         metadata={"source": job["path"], "loc": {"pageNumber": number}})
                for number, page in enumerate(reader.pages, start=1)]
    except (PyPdfError, ValueError) as error:
        message = str(error)
        raise ValueError(message if message.startswith("Invalid PDF") else "Invalid PDF: the file could not be read.") from None


def prepare_documents(job: dict, text: Optional[str], storage_root: Path) -> list[Document]:
    pages = [page for page in load_pages(job, text, storage_root) if page.page_content.strip()]
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks = splitter.split_documents(pages)
    if not chunks:
        raise ValueError(NO_TEXT_ERROR)
    for chunk in chunks:
        chunk.metadata = {**chunk.metadata, "ownerId": job["ownerId"], "documentId": job["documentId"],
                          "fileName": job["filename"], "sourceType": job["sourceType"],
                          "uploadedAt": job["uploadedAt"]}
    return chunks


def point_id(document_id: str, index: int) -> str:
    """Stable id per chunk, so re-running an interrupted job overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"docquery:{document_id}:{index}"))


def run_indexing(job_id: str, text: Optional[str], *, jobs: UploadJobs, make_embeddings: Callable,
                 make_store: Callable, storage_root: Path) -> None:
    job = jobs.get(job_id)
    if job is None or job["state"] in ("completed", "failed"):
        return  # Deleted, or a repeated delivery of a job that already finished.

    def progress(phase: str, completed: int, total: int) -> None:
        jobs.update(job_id, state="active", progress={"phase": phase, "completed": completed, "total": total})

    try:
        logger.info("Processing upload job %s", job_id)
        progress("reading", 0, 0)
        if not str(job["ownerId"]).startswith("user_"):
            raise ValueError("Upload job has no owner.")
        chunks = prepare_documents(job, text, storage_root)
        total = len(chunks)
        embeddings = make_embeddings()
        progress("embedding", 0, total)
        vectors = []
        for chunk in chunks:
            vectors.append(embeddings.embed_document(chunk.page_content))
            progress("embedding", len(vectors), total)
        progress("saving", total, total)
        store = make_store()
        store.ensure_collection(len(vectors[0]))
        store.upsert([{"id": point_id(job["documentId"], index), "vector": vector,
                       "payload": {"content": chunk.page_content, "metadata": chunk.metadata}}
                      for index, (chunk, vector) in enumerate(zip(chunks, vectors))])
        progress("ready", total, total)
        jobs.update(job_id, state="completed")
        logger.info("All chunks for upload job %s are in the vector store", job_id)
    except Exception as error:
        reason = (str(error) or type(error).__name__)[:1000]
        jobs.update(job_id, state="failed", error=reason)
        logger.error("Upload job %s failed. %s", job_id, failure_message(reason))
