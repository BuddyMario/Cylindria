from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Optional

from .models import JobStatusResponse


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobStatusResponse] = {}

    @staticmethod
    def _normalize_progress(progress: float | int | None) -> int | None:
        if progress is None:
            return None
        try:
            value = float(progress)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        value = max(0.0, min(100.0, value))
        return int(round(value))

    def upsert(
        self,
        job_id: str,
        state: str,
        detail: str | None = None,
        prompt_id: str | None = None,
        progress: float | int | None = None,
        gpu_id: int | None = None,
    ) -> JobStatusResponse:
        now = datetime.now(timezone.utc)
        normalized_progress = self._normalize_progress(progress)
        completed_without_progress = normalized_progress is None and state == 'completed'

        if job_id in self._jobs:
            js = self._jobs[job_id]
            js.state = state
            js.updated_at = now
            if gpu_id is not None:
                js.gpu_id = gpu_id
            if detail is not None:
                js.detail = detail
            if prompt_id is not None:
                js.prompt_id = prompt_id
            if normalized_progress is not None:
                js.progress = max(js.progress, normalized_progress)
            elif completed_without_progress:
                js.progress = max(js.progress, 100)
        else:
            initial_progress = 100 if completed_without_progress else (normalized_progress or 0)
            js = JobStatusResponse(
                job_id=job_id,
                state=state,
                gpu_id=gpu_id,
                submitted_at=now,
                updated_at=now,
                progress=initial_progress,
                detail=detail,
                prompt_id=prompt_id,
            )
            self._jobs[job_id] = js
        return js

    def get(self, job_id: str) -> Optional[JobStatusResponse]:
        return self._jobs.get(job_id)

    def find_by_prompt_id(self, prompt_id: str, gpu_id: int | None = None) -> Optional[JobStatusResponse]:
        for job in self._jobs.values():
            if job.prompt_id != prompt_id:
                continue
            if gpu_id is not None and job.gpu_id is not None and job.gpu_id != gpu_id:
                continue
            return job
        return None


