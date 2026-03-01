from __future__ import annotations
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4
from .models import IndexJobStatus

class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, IndexJobStatus] = {}
        self._lock = Lock()

    def create(self) -> IndexJobStatus:
        job_id = str(uuid4())
        job = IndexJobStatus(job_id=job_id, status="queued", message="Queued")
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> IndexJobStatus | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: object) -> IndexJobStatus | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            current = job.model_dump()
            current.update(fields)
            current["updated_at"] = datetime.now(timezone.utc)
            updated = IndexJobStatus(**current)
            self._jobs[job_id] = updated
            return updated
