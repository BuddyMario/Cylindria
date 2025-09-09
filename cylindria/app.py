from fastapi import FastAPI, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from .config import Settings, get_settings
from .security import require_api_key
from .comfy_client import ComfyClient
from .jobs import JobStore
from .models import StartJobResponse, JobStatusResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(title="Cylindria", version="0.1.0")

    job_store = JobStore()
    comfy = ComfyClient(base_url=settings.comfyui_base_url, job_store=job_store)

    @app.get("/serverstatus")
    async def server_status(api_key: str | None = Depends(require_api_key), settings: Settings = Depends(get_settings)):
        reachable = await comfy.ping()
        return {
            "status": "ok" if reachable else "degraded",
            "comfy_url": str(settings.comfyui_base_url),
            "reachable": reachable,
        }

    @app.put("/startjob/{job_id}/", response_model=StartJobResponse)
    async def start_job(job_id: str, workflow: dict, api_key: str | None = Depends(require_api_key)):
        accepted, detail = await comfy.submit_workflow(job_id, workflow)
        return StartJobResponse(job_id=job_id, accepted=accepted, detail=detail)

    @app.get("/jobstatus/{job_id}/", response_model=JobStatusResponse)
    async def job_status(job_id: str, api_key: str | None = Depends(require_api_key)):
        status_obj = job_store.get(job_id)
        if not status_obj:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return status_obj

    return app

