"""Listing and deleting documents (mirrors documents.js and delete-document.js).

Qdrant stays the source of truth: a document is the set of chunks that share a
documentId, so there is no separate document table to keep in sync.
"""

import hashlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.ai import QdrantStore
from app.errors import ApiError
from app.jobs import UploadJobs

PAYLOAD_FIELDS = ["metadata.documentId", "metadata.fileName", "metadata.source", "metadata.sourceType",
                  "metadata.uploadedAt", "metadata.ownerId"]


def legacy_document_id(source: str) -> str:
    """Older uploads have no documentId; their stored path identifies them instead."""
    return f"legacy-{hashlib.sha256(source.encode()).hexdigest()}"


def _iso(milliseconds: float) -> str:
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_date(value: str) -> Optional[str]:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _iso(moment.timestamp() * 1000)


def document_from_metadata(metadata: Optional[dict]) -> Optional[dict]:
    metadata = metadata or {}
    source = metadata.get("source")
    if not metadata.get("documentId") and not source:
        return None
    basename = re.split(r"[\\/]", source)[-1] if source else None
    document_id = metadata.get("documentId") or legacy_document_id(source)
    filename = metadata.get("fileName") or (re.sub(r"^\d+-\d+-", "", basename) if basename else "PDF")
    legacy_timestamp = re.match(r"^(\d{13})-\d+-", basename) if basename else None
    if metadata.get("uploadedAt"):
        uploaded_at = _parse_date(metadata["uploadedAt"])
    else:
        uploaded_at = _iso(int(legacy_timestamp.group(1))) if legacy_timestamp else None
    must = [{"key": "metadata.documentId" if metadata.get("documentId") else "metadata.source",
             "match": {"value": metadata.get("documentId") or source}}]
    if metadata.get("ownerId"):
        must.append({"key": "metadata.ownerId", "match": {"value": metadata["ownerId"]}})
    return {
        "id": document_id,
        "ownerId": metadata.get("ownerId"),
        "filename": filename,
        "sourceType": "text" if metadata.get("sourceType") == "text" else "pdf",
        "uploadedAt": uploaded_at,
        "sourcePaths": [source] if source else [],
        "chunkCount": 0,
        "filter": {"must": must},
    }


def list_documents(store: QdrantStore, owner_id: Optional[str] = None) -> list[dict]:
    """All documents, or only one owner's. Reads chunk metadata only, never text or vectors."""
    owner_filter = {"must": [{"key": "metadata.ownerId", "match": {"value": owner_id}}]} if owner_id else None
    documents: dict[str, dict] = {}
    for point in store.scroll(PAYLOAD_FIELDS, owner_filter):
        document = document_from_metadata((point.get("payload") or {}).get("metadata"))
        if document is None or (owner_id and document["ownerId"] != owner_id):
            continue
        existing = documents.setdefault(document["id"], document)
        existing["chunkCount"] += 1
        for source in document["sourcePaths"]:
            if source not in existing["sourcePaths"]:
                existing["sourcePaths"].append(source)
    return sorted(documents.values(), key=lambda doc: (doc["filename"].casefold(), doc["filename"], doc["id"]))


def _conflict(message: str) -> ApiError:
    return ApiError(409, message)


def _resolve(storage_root: Path, source: str) -> Path:
    # Node stored "uploads/<file>" (or "uploads\\<file>" on Windows) relative to its folder.
    return Path(os.path.abspath(storage_root / source.replace("\\", "/")))


def _inside(directory: Path, file: Path) -> bool:
    try:
        relative = file.relative_to(directory)
    except ValueError:
        return False
    return relative != Path(".")


def stored_pdf_path(source: str, storage_root: Path) -> Optional[Path]:
    """The stored file for `source`, if it safely exists inside uploads/.

    Only paths from stored metadata or jobs reach here, never from the request.
    Directories, links, and anything outside uploads/ are refused.
    """
    uploads = Path(os.path.abspath(storage_root / "uploads"))
    file = _resolve(storage_root, source)
    if not _inside(uploads, file):
        raise _conflict("The stored PDF path is outside uploads. Remove it manually after checking the file.")
    try:
        info = os.lstat(file)
    except FileNotFoundError:
        return None  # Already removed; this must not block cleanup.
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise _conflict("The stored PDF is not a regular file. Check it manually.")
    if not _inside(uploads.resolve(), file.resolve()):
        raise _conflict("The stored PDF path is outside uploads. Check it manually.")
    return file


def delete_document(document: dict, all_documents: list[dict], *, jobs: UploadJobs, store: QdrantStore,
                    storage_root: Path) -> None:
    """Remove a document's upload jobs, stored file, and vectors.

    Every check runs before anything is changed. The document stays listed until
    the vectors are gone, so a failed delete can simply be retried.
    """
    matching_jobs = jobs.for_document(document["ownerId"], document["id"]) if document["ownerId"] else []
    if any(job["state"] not in ("completed", "failed") for job in matching_jobs):
        raise _conflict("This document is still indexing. Wait until indexing finishes before deleting it.")
    sources = list(document["sourcePaths"]) + [job["path"] for job in matching_jobs
                                               if job["path"] and job["path"] not in document["sourcePaths"]]
    files = []
    for source in sources:
        target = _resolve(storage_root, source)
        if any(other["id"] != document["id"] and any(_resolve(storage_root, value) == target
                                                     for value in other["sourcePaths"])
               for other in all_documents):
            raise _conflict("Another document shares this stored file. Check the uploads before deleting it.")
        safe = stored_pdf_path(source, storage_root)
        if safe:
            files.append(safe)
    for job in matching_jobs:
        jobs.remove(job["id"])
    for file in files:
        try:
            file.unlink()
        except FileNotFoundError:
            pass
    store.delete(document["filter"])
