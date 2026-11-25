"""Celery app and the document indexing task.

Start the worker from the server-py folder (on Windows, --pool=solo is required):
    celery -A app.tasks worker --pool=solo --loglevel=info
"""

import logging
from typing import Optional

from celery import Celery
from celery.signals import worker_init, worker_ready

from app import settings
from app.ai import GeminiEmbeddings, QdrantStore
from app.ingestion import run_indexing
from app.jobs import UploadJobs

celery_app = Celery("docquery", broker=settings.VALKEY_URL)
celery_app.conf.update(
    task_default_queue="docquery-indexing",
    # Acknowledge a job only after it finishes, so a crashed worker's job is delivered
    # again instead of lost (BullMQ did the same with stalled jobs).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Take one job at a time, like the Node worker's concurrency: 1.
    worker_prefetch_multiplier=1,
    # Status lives in the upload_jobs table, so Celery results are not stored.
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="docquery.index_document")
def index_document(job_id: str, text: Optional[str] = None) -> None:
    run_indexing(job_id, text, jobs=UploadJobs(settings.DATABASE_PATH), make_embeddings=GeminiEmbeddings,
                 make_store=QdrantStore, storage_root=settings.STORAGE_ROOT)


def enqueue_indexing(job_id: str, text: Optional[str] = None) -> None:
    # The Celery task id is the job id, so the two can always be matched up.
    index_document.apply_async(args=[job_id, text], task_id=job_id)


@worker_init.connect
def check_configuration(**_kwargs) -> None:
    settings.gemini_api_key()  # Stop at startup instead of failing every job.


@worker_ready.connect
def announce_ready(**_kwargs) -> None:
    logging.getLogger("docquery.worker").info("PDF worker ready")
