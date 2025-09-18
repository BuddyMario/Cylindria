from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from .models import JobStatusResponse


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobStatusResponse] = {}

    def upsert(self, job_id: str, state: str, detail: str | None = None, prompt_id: str | None = None) -> JobStatusResponse:
        now = datetime.now(timezone.utc)
        if job_id in self._jobs:
            js = self._jobs[job_id]
            js.state = state
            js.updated_at = now
            if detail is not None:
                js.detail = detail
            if prompt_id is not None:
                js.prompt_id = prompt_id
        else:
            js = JobStatusResponse(
                job_id=job_id,
                state=state,
                submitted_at=now,
                updated_at=now,
                detail=detail,
                prompt_id=prompt_id,
            )
            self._jobs[job_id] = js
        return js

    def get(self, job_id: str) -> Optional[JobStatusResponse]:
        return self._jobs.get(job_id)

