from datetime import datetime
from pydantic import BaseModel, Field


class StartJobResponse(BaseModel):
    job_id: str
    accepted: bool = True
    detail: str | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    state: str = Field(description="queued|submitted|running|completed|failed|unknown")
    submitted_at: datetime
    updated_at: datetime
    detail: str | None = None

