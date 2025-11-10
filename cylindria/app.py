import asyncio
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import Depends, FastAPI, HTTPException, Query, status

from .comfy_client import ComfyClient
from .config import Settings, get_settings
from .jobs import JobStore
from .models import JobStatusResponse, StartJobResponse
from .security import require_api_key


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


def _format_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _build_gpu_base_url(base_url: str, gpu_id: int, default_port: int = 8188) -> str:
    """Derive the ComfyUI URL for a specific GPU by incrementing the base port."""
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    base_port = parsed.port if parsed.port is not None else default_port
    target_port = base_port + gpu_id
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    netloc = f"{auth}{_format_host(host)}:{target_port}"
    path = (parsed.path or "").rstrip("/")
    rebuilt = parsed._replace(netloc=netloc, path=path)
    return urlunparse(rebuilt)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    job_store = JobStore()
    dev_dir = settings.dev_save_dir if getattr(settings, "dev_mode", False) else None
    comfy_clients: dict[int, ComfyClient] = {}
    for gpu_id in range(settings.number_of_gpus):
        gpu_base_url = _build_gpu_base_url(settings.comfyui_base_url, gpu_id)
        comfy_clients[gpu_id] = ComfyClient(
            base_url=gpu_base_url,
            job_store=job_store,
            gpu_id=gpu_id,
            dev_save_dir=dev_dir,
        )

    async def _start_clients():
        if not comfy_clients:
            return
        await asyncio.gather(*(client.ensure_ws_listener() for client in comfy_clients.values()))

    async def _stop_clients():
        if not comfy_clients:
            return
        await asyncio.gather(*(client.stop_ws_listener() for client in comfy_clients.values()))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await _start_clients()
        try:
            yield
        finally:
            await _stop_clients()

    app = FastAPI(title="Cylindria", version="0.1.0", lifespan=lifespan)
    max_gpu_id = max(0, settings.number_of_gpus - 1)

    @app.get("/serverstatus")
    async def server_status(
        gpu_id: int = Query(0, ge=0, le=max_gpu_id, alias="GpuId"),
        api_key: str | None = Depends(require_api_key),
    ):
        client = comfy_clients[gpu_id]
        reachable = await client.ping()
        return {
            "status": "ok" if reachable else "degraded",
            "comfy_url": str(client.base_url),
            "reachable": reachable,
            "gpu_id": gpu_id,
        }


    @app.put("/startjob/{job_id}/", response_model=StartJobResponse)
    async def start_job(
        job_id: str,
        workflow: dict[str, Any],
        gpu_id: int = Query(0, ge=0, le=max_gpu_id, alias="GpuId"),
        api_key: str | None = Depends(require_api_key),
    ):
        client = comfy_clients[gpu_id]
        normalized_workflow = _normalize_workflow_payload(workflow)
        accepted, detail = await client.submit_workflow(job_id, normalized_workflow)
        return StartJobResponse(job_id=job_id, accepted=accepted, detail=detail, gpu_id=gpu_id)

    @app.get("/jobstatus/{job_id}/", response_model=JobStatusResponse)
    async def job_status(job_id: str, api_key: str | None = Depends(require_api_key)):
        status_obj = job_store.get(job_id)
        if not status_obj:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return status_obj

    return app
