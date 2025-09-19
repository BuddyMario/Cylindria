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
    progress: int = Field(default=0, ge=0, le=100)
    detail: str | None = None
    prompt_id: str | None = None
