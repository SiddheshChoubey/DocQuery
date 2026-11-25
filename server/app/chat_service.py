"""Runs one answer at a time per chat and records the result (mirrors chat-service.js).

A turn is started synchronously, so every validation error is returned before
streaming begins. The answer then runs as an asyncio task: Stop, or the client
disconnecting, cancels that task, which also cancels the Gemini request.
"""

import asyncio
import threading
from typing import Awaitable, Callable, Optional

from app.chat import NO_PASSAGES_MESSAGE, finalize_answer
from app.conversations import ConversationStore, contextual_query, conversation_context
from app.errors import ApiError, error_status

STOPPED_MESSAGE = "Answer stopped. You can retry this question."
QUOTA_MESSAGE = "Gemini quota reached. Wait and retry."
FAILED_MESSAGE = "Unable to generate an answer. Check Gemini and local services, then retry."

EventSink = Callable[[dict], Awaitable[None]]


class ChatService:
    def __init__(self, store: ConversationStore, *, find_document: Callable, retrieve: Callable,
                 answer: Callable, rewrite: Optional[Callable] = None):
        self.store = store
        self.find_document = find_document  # (owner_id, document_id) -> document or None   [blocking]
        self.retrieve = retrieve            # (document, query, limit) -> passages            [blocking]
        self.answer = answer                # async (question, passages, document, **options) -> result
        self.rewrite = rewrite              # async (question, history) -> query
        self.active: dict[str, dict] = {}   # conversation id -> {"task", "documentId"}
        self.deleting_documents: set[str] = set()
        self._lock = threading.Lock()       # Document deletion runs in a worker thread.

    # --- Document deletion guard ---

    def check_document(self, document_id: str) -> None:
        if document_id in self.deleting_documents:
            raise ApiError(409, "This document is being deleted.")

    def begin_document_deletion(self, document_id: str) -> None:
        with self._lock:
            self.check_document(document_id)
            if any(entry["documentId"] == document_id for entry in self.active.values()):
                raise ApiError(409, "Stop active answers for this document before deleting it.")
            self.deleting_documents.add(document_id)

    def end_document_deletion(self, document_id: str) -> None:
        with self._lock:
            self.deleting_documents.discard(document_id)

    # --- Answers ---

    def stop(self, conversation_id: str, document_id: str, owner_id: str) -> None:
        self.store.get(conversation_id, document_id, owner_id)  # 404 unless it's this user's chat.
        entry = self.active.get(conversation_id)
        if entry:
            entry["task"].cancel()

    def start(self, conversation_id: str, document_id: str, body: dict, owner_id: str,
              on_event: Optional[EventSink] = None) -> asyncio.Task:
        """Validate and save the new turn, then start answering it in the background."""
        with self._lock:
            self.check_document(document_id)
            self.store.get(conversation_id, document_id, owner_id)
            if conversation_id in self.active:
                raise ApiError(409, "An answer is already being generated in this chat.")
            turn = self.store.begin_turn(conversation_id, document_id, body, owner_id)
            task = asyncio.get_running_loop().create_task(
                self._run(conversation_id, document_id, turn, owner_id, on_event))
            self.active[conversation_id] = {"task": task, "documentId": document_id}
        task.add_done_callback(lambda done: self._cancelled_before_start(done, conversation_id, turn["id"]))
        return task

    def _cancelled_before_start(self, task: asyncio.Task, conversation_id: str, turn_id: str) -> None:
        # A task cancelled before its first step never runs _run's cleanup, so do it here.
        if task.cancelled():
            self._save_unfinished(turn_id, "stopped", STOPPED_MESSAGE, "", [])
            with self._lock:
                self.active.pop(conversation_id, None)

    async def _run(self, conversation_id: str, document_id: str, turn: dict, owner_id: str,
                   on_event: Optional[EventSink]) -> dict:
        partial, sources = "", []

        async def progress(event: dict) -> None:
            nonlocal partial, sources
            if event["type"] == "sources":
                sources = event["sources"]
            elif event["type"] == "delta":
                partial += event["text"]
            await on_event(event)

        try:
            if on_event:
                await on_event({"type": "started", "turnId": turn["id"]})
            history = conversation_context(self.store.get(conversation_id, document_id, owner_id)["turns"])
            query = (await self.rewrite(turn["question"], history) if history and self.rewrite
                     else contextual_query(turn["question"], history))
            document = await asyncio.to_thread(self.find_document, owner_id, document_id)
            if document is None:
                raise ApiError(404, "This document is not indexed. Refresh the document list.")
            limit = 5 if turn["answerMode"] == "detailed" else 2
            passages = await asyncio.to_thread(self.retrieve, document, query, limit)
            if passages:
                result = await self.answer(turn["question"], passages, document, history=history,
                                           on_event=progress if on_event else None,
                                           answer_mode=turn["answerMode"])
            else:
                result = {"message": NO_PASSAGES_MESSAGE, "sources": [], "citationNote": None}
            self.store.finish_turn(turn["id"], status="completed", answer=result["message"],
                                   sources=result["sources"], citation_note=result.get("citationNote"))
        except asyncio.CancelledError:
            self._save_unfinished(turn["id"], "stopped", STOPPED_MESSAGE, partial, sources)
        except Exception as error:
            if isinstance(error, ApiError):
                message = error.message
            else:
                message = QUOTA_MESSAGE if error_status(error) == 429 else FAILED_MESSAGE
            self._save_unfinished(turn["id"], "failed", message, partial, sources)
        finally:
            with self._lock:
                self.active.pop(conversation_id, None)
        return self.store.get(conversation_id, document_id, owner_id)

    def _save_unfinished(self, turn_id: str, status: str, message: str, partial: str, sources: list) -> None:
        incomplete = finalize_answer(partial, sources)
        self.store.finish_turn(turn_id, status=status, error=message, answer=partial,
                               sources=incomplete["sources"],
                               citation_note=("Incomplete answer. " + (incomplete["citationNote"] or ""))
                               if partial else None)
