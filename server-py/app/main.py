"""DocQuery API (Python).

Run from the server-py folder:
    uvicorn app.main:app --port 8000 --reload
"""

import json
import os
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import UploadFile

from app import settings
from app.auth import ClerkAuth
from app.errors import (INVALID_JSON_MESSAGE, ApiError, NoStoreMiddleware, SafeErrorsMiddleware,
                        register_error_handlers)
from app.jobs import UploadJobs, failure_message, now_iso

JSON_BODY_LIMIT = 512 * 1024
TEXT_TOO_LARGE = "Pasted text is too large. Use at most 50,000 characters."


def client_origins() -> list[str]:
    raw = os.getenv("CLIENT_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def validate_text_input(body: dict) -> Optional[str]:
    title, text = body.get("title"), body.get("text")
    if not isinstance(title, str) or not title.strip():
        return "Enter a title."
    if len(title.strip()) > 120:
        return "Keep the title within 120 characters."
    if not isinstance(text, str) or not text.strip():
        return "Paste some text to index."
    if len(text) > 50000:
        return "Keep pasted text within 50,000 characters."
    return None


async def read_json_body(request: Request) -> dict:
    """Parse a JSON body with the same limits and messages as express.json()."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > JSON_BODY_LIMIT:
        raise ApiError(413, TEXT_TOO_LARGE)
    raw = await request.body()
    if len(raw) > JSON_BODY_LIMIT:
        raise ApiError(413, TEXT_TOO_LARGE)
    if not request.headers.get("content-type", "").lower().startswith("application/json") or not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise ApiError(400, INVALID_JSON_MESSAGE) from None
    if not isinstance(body, (dict, list)):  # express.json() only accepts objects and arrays.
        raise ApiError(400, INVALID_JSON_MESSAGE)
    return body if isinstance(body, dict) else {}


def stored_filename(original: str) -> str:
    # Same pattern as the Node server: <epoch ms>-<random>-<original name>.
    # Only the final name part is kept, so a crafted name can't escape uploads/.
    name = Path(original.replace("\\", "/")).name.strip() or "upload.pdf"
    return f"{int(time.time() * 1000)}-{round(random.random() * 1e9)}-{name}"


def default_enqueue(job_id: str, text: Optional[str] = None) -> None:
    from app.tasks import enqueue_indexing  # Imported here so tests never need Celery or Valkey.

    enqueue_indexing(job_id, text)


def create_app(auth: Optional[ClerkAuth] = None, jobs: Optional[UploadJobs] = None,
               enqueue: Optional[Callable] = None, uploads_dir: Optional[Path] = None) -> FastAPI:
    origins = client_origins()
    app = FastAPI(title="DocQuery API", docs_url=None, redoc_url=None, openapi_url=None)
    # Every route except the health check depends on this; it returns the owner id.
    app.state.auth = auth = auth or ClerkAuth.from_env(origins)
    jobs = jobs or UploadJobs(settings.DATABASE_PATH)
    enqueue = enqueue or default_enqueue
    uploads = Path(uploads_dir or settings.UPLOADS_DIR)

    # Middleware added last runs first: CORS wraps everything, so even error
    # responses carry CORS headers.
    app.add_middleware(SafeErrorsMiddleware)
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    register_error_handlers(app)

    def queue_job(owner_id: str, source_type: str, filename: str, path: Optional[str],
                  text: Optional[str] = None) -> dict:
        job_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
        jobs.create(job_id=job_id, owner_id=owner_id, document_id=document_id, source_type=source_type,
                    filename=filename, path=path, uploaded_at=now_iso())
        try:
            enqueue(job_id, text)
        except Exception:
            jobs.remove(job_id)  # Nothing was queued, so don't leave a job that never runs.
            raise
        return {"jobId": job_id, "documentId": document_id}

    @app.get("/")
    def health():
        return {"status": "All Good!"}

    @app.post("/upload/pdf", status_code=202)
    async def upload_pdf(request: Request, owner_id: str = Depends(auth)):
        try:
            form = await request.form()
        except Exception:
            form = None
        try:
            upload = form.get("pdf") if form is not None else None
            if not isinstance(upload, UploadFile) or not upload.filename:
                raise ApiError(400, "Select a PDF to upload.")
            name = stored_filename(upload.filename)

            def save() -> None:
                uploads.mkdir(parents=True, exist_ok=True)
                with open(uploads / name, "wb") as destination:
                    shutil.copyfileobj(upload.file, destination)

            await run_in_threadpool(save)
        finally:
            if form is not None:
                await form.close()
        # Stored relative to the backend folder, as the Node server did ("uploads/<file>").
        queued = await run_in_threadpool(queue_job, owner_id, "pdf", upload.filename, f"uploads/{name}")
        return {"message": "PDF uploaded; queued for indexing.", **queued}

    @app.post("/upload/text", status_code=202)
    async def upload_text(request: Request, owner_id: str = Depends(auth)):
        body = await read_json_body(request)
        error = validate_text_input(body)
        if error:
            raise ApiError(400, error)
        queued = await run_in_threadpool(queue_job, owner_id, "text", body["title"].strip(), None,
                                         body["text"].strip())
        return {"message": "Text queued for indexing.", **queued}

    @app.get("/upload/status/{job_id}")
    def upload_status(job_id: str, owner_id: str = Depends(auth)):
        job = jobs.get(job_id)
        if job is None:
            raise ApiError(404, "Upload job not found. Submit the document again.")
        if job["ownerId"] != owner_id:
            raise ApiError(404, "Upload job not found.")
        result = {"state": job["state"], "documentId": job["documentId"],
                  # BullMQ reported 0 until the worker first updated progress.
                  "progress": job["progress"] if job["progress"] is not None else 0}
        if job["state"] == "failed":
            result["message"] = failure_message(job["error"] or "")
        return result

    return app


app = create_app()
