from typing import Any

from fastapi import FastAPI, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from .config import Settings, get_settings
from .security import require_api_key
from .comfy_client import ComfyClient
from .jobs import JobStore
from .models import StartJobResponse, JobStatusResponse



def _looks_like_node_definition(candidate: Any) -> bool:
    return isinstance(candidate, dict) and ("class_type" in candidate or "inputs" in candidate)


def _normalize_workflow_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(workflow, dict):
        return {"prompt": workflow}

    prompt_section = workflow.get("prompt")
    if isinstance(prompt_section, dict):
        nodes_outside_prompt = {
            key: value
            for key, value in workflow.items()
            if key != "prompt" and _looks_like_node_definition(value)
        }
        if not nodes_outside_prompt:
            return workflow
        merged_prompt = dict(prompt_section)
        merged_prompt.update(nodes_outside_prompt)
        normalized = {key: value for key, value in workflow.items() if key not in nodes_outside_prompt}
        normalized["prompt"] = merged_prompt
        return normalized

    nodes: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    for key, value in workflow.items():
        if _looks_like_node_definition(value):
            nodes[key] = value
        else:
            metadata[key] = value

    if nodes:
        normalized: dict[str, Any] = dict(metadata)
        normalized["prompt"] = nodes
        return normalized

    return {"prompt": workflow}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(title="Cylindria", version="0.1.0")

    job_store = JobStore()
    dev_dir = settings.dev_save_dir if getattr(settings, "dev_mode", False) else None
    comfy = ComfyClient(base_url=settings.comfyui_base_url, job_store=job_store, dev_save_dir=dev_dir)

    @app.get("/serverstatus")
    async def server_status(api_key: str | None = Depends(require_api_key), settings: Settings = Depends(get_settings)):
        reachable = await comfy.ping()
        return {
            "status": "ok" if reachable else "degraded",
            "comfy_url": str(settings.comfyui_base_url),
            "reachable": reachable,
        }

    @app.put("/startjob/{job_id}/", response_model=StartJobResponse)
    async def start_job(job_id: str, workflow: dict[str, Any], api_key: str | None = Depends(require_api_key)):
        normalized_workflow = _normalize_workflow_payload(workflow)
        accepted, detail = await comfy.submit_workflow(job_id, normalized_workflow)
        return StartJobResponse(job_id=job_id, accepted=accepted, detail=detail)

    @app.get("/jobstatus/{job_id}/", response_model=JobStatusResponse)
    async def job_status(job_id: str, api_key: str | None = Depends(require_api_key)):
        status_obj = job_store.get(job_id)
        if not status_obj:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return status_obj

    return app
