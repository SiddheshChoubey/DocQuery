"""DocQuery API (Python).

Run from the server folder:
    uvicorn app.main:app --port 8000 --reload
"""

import asyncio
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
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from app import settings
from app.ai import GeminiEmbeddings, QdrantStore
from app.auth import ClerkAuth
from app.chat import answer_with_sources, create_chat_model, retrieve_passages, rewrite_query
from app.chat_service import ChatService
from app.conversations import ConversationStore
from app.documents import delete_document, list_documents
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


def valid_document_id(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ApiError(400, "Select a document.")
    return value


def ndjson(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


async def stream_events(queue: asyncio.Queue, task: asyncio.Task, load_conversation: Callable):
    """Send each event as one JSON line, then a final "done" line with the saved chat.

    If the client disconnects, this generator is closed and the answer is
    cancelled, which saves it as stopped (like pressing Stop).
    """
    getter = None
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter.done():
                yield ndjson(getter.result())
                continue
            getter.cancel()
            while not queue.empty():
                yield ndjson(queue.get_nowait())
            conversation = task.result() if not task.cancelled() else load_conversation()
            yield ndjson({"type": "done", "conversation": conversation})
            return
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
        if not task.done():
            task.cancel()


def default_enqueue(job_id: str, text: Optional[str] = None) -> None:
    from app.tasks import enqueue_indexing  # Imported here so tests never need Celery or Valkey.

    enqueue_indexing(job_id, text)


def create_app(auth: Optional[ClerkAuth] = None, jobs: Optional[UploadJobs] = None,
               enqueue: Optional[Callable] = None, storage_root: Optional[Path] = None,
               store: Optional[QdrantStore] = None,
               conversations: Optional[ConversationStore] = None,
               chat_model=None, rewrite_model=None, embeddings=None) -> FastAPI:
    origins = client_origins()
    app = FastAPI(title="DocQuery API", docs_url=None, redoc_url=None, openapi_url=None)
    # Every route except the health check depends on this; it returns the owner id.
    app.state.auth = auth = auth or ClerkAuth.from_env(origins)
    jobs = jobs or UploadJobs(settings.DATABASE_PATH)
    enqueue = enqueue or default_enqueue
    storage_root = Path(storage_root or settings.STORAGE_ROOT)
    uploads = storage_root / "uploads"
    store = store or QdrantStore()
    conversations = conversations or ConversationStore(settings.DATABASE_PATH)
    if chat_model is None:
        settings.gemini_api_key()  # Like the Node API, refuse to start without a Gemini key.

    # Gemini clients are created on first use, so importing the app stays fast and offline.
    clients = {"chat": chat_model, "rewrite": rewrite_model, "embeddings": embeddings}

    def client(name: str):
        if clients[name] is None:
            clients[name] = {"chat": lambda: create_chat_model(max_retries=2),
                             "rewrite": lambda: create_chat_model(max_retries=0),
                             "embeddings": GeminiEmbeddings}[name]()
        return clients[name]

    def find_document(owner_id: str, document_id: str):
        return next((doc for doc in list_documents(store, owner_id) if doc["id"] == document_id), None)

    async def answer(question, passages, document, **options):
        return await answer_with_sources(client("chat"), question, passages, document, **options)

    async def rewrite(question, history):
        return await rewrite_query(client("rewrite"), question, history)

    app.state.chat = chat = ChatService(
        conversations, find_document=find_document,
        retrieve=lambda document, query, limit: retrieve_passages(document, query, limit,
                                                                  embeddings=client("embeddings"), store=store),
        answer=answer, rewrite=rewrite)

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

    @app.get("/documents")
    def get_documents(owner_id: str = Depends(auth)):
        fields = ("id", "filename", "chunkCount", "sourceType", "uploadedAt")
        return {"documents": [{field: document[field] for field in fields}
                              for document in list_documents(store, owner_id)]}

    @app.delete("/documents/{document_id}")
    def remove_document(document_id: str, owner_id: str = Depends(auth)):
        document = next((doc for doc in list_documents(store, owner_id) if doc["id"] == document_id), None)
        if document is None:
            raise ApiError(404, "Document not found. Refresh the document list.")
        chat.begin_document_deletion(document["id"])
        try:
            # The shared-file check needs every owner's documents, as in the Node server.
            delete_document(document, list_documents(store), jobs=jobs, store=store, storage_root=storage_root)
            conversations.remove_document(document["id"], owner_id)
        finally:
            chat.end_document_deletion(document["id"])
        return {"message": "Document deleted.", "documentId": document["id"]}

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

    # --- Conversations ---

    @app.get("/conversations")
    def list_conversations(documentId: Optional[str] = None, owner_id: str = Depends(auth)):
        return {"conversations": conversations.list(valid_document_id(documentId), owner_id)}

    @app.post("/conversations", status_code=201)
    async def create_conversation(request: Request, owner_id: str = Depends(auth)):
        document_id = valid_document_id((await read_json_body(request)).get("documentId"))
        if await run_in_threadpool(find_document, owner_id, document_id) is None:
            raise ApiError(404, "This document is not indexed.")
        chat.check_document(document_id)
        return {"conversation": conversations.create(document_id, owner_id)}

    @app.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, documentId: Optional[str] = None, owner_id: str = Depends(auth)):
        return {"conversation": conversations.get(conversation_id, valid_document_id(documentId), owner_id)}

    @app.delete("/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, documentId: Optional[str] = None,
                            owner_id: str = Depends(auth)):
        document_id = valid_document_id(documentId)
        conversations.get(conversation_id, document_id, owner_id)
        if conversation_id in chat.active:
            raise ApiError(409, "Stop the answer before deleting this chat.")
        conversations.remove(conversation_id, document_id, owner_id)
        return {"message": "Conversation deleted."}

    @app.post("/conversations/{conversation_id}/stop")
    async def stop_answer(conversation_id: str, request: Request, owner_id: str = Depends(auth)):
        chat.stop(conversation_id, valid_document_id((await read_json_body(request)).get("documentId")), owner_id)
        return {"message": "Stop requested."}

    @app.post("/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, request: Request, owner_id: str = Depends(auth)):
        body = await read_json_body(request)
        document_id = valid_document_id(body.get("documentId"))
        if "retry" in body and not isinstance(body["retry"], bool):
            raise ApiError(400, "Invalid retry flag.")

        def saved_conversation():
            return conversations.get(conversation_id, document_id, owner_id)

        if request.headers.get("accept") != "application/x-ndjson":
            task = chat.start(conversation_id, document_id, body, owner_id)
            await asyncio.wait({task})
            return {"conversation": task.result() if not task.cancelled() else saved_conversation()}
        events: asyncio.Queue = asyncio.Queue()
        # Validation errors are raised here, before the stream starts, as normal JSON errors.
        task = chat.start(conversation_id, document_id, body, owner_id, on_event=events.put)
        return StreamingResponse(stream_events(events, task, saved_conversation),
                                 media_type="application/x-ndjson; charset=utf-8",
                                 headers={"X-Accel-Buffering": "no"})

    return app


app = create_app()
