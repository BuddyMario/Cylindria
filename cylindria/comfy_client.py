from __future__ import annotations

import json
from typing import Any, Tuple

import httpx

from .jobs import JobStore


class ComfyClient:
    """Thin helper to talk to ComfyUI, with graceful fallbacks.

    Default ComfyUI base URL is typically http://127.0.0.1:8188.
    """

    def __init__(self, base_url: str, job_store: JobStore) -> None:
        self.base_url = base_url.rstrip("/")
        self.job_store = job_store
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))

    async def ping(self) -> bool:
        try:
            # Try a lightweight request; ComfyUI may not provide a canonical health endpoint.
            # We attempt root; if that fails, we still return False without raising.
            resp = await self._client.get(f"{self.base_url}/")
            return resp.status_code < 500
        except Exception:
            return False

    async def submit_workflow(self, job_id: str, workflow: dict[str, Any]) -> Tuple[bool, str | None]:
        """Try to forward a workflow to ComfyUI.

        Many ComfyUI deployments accept POST /prompt with a JSON body.
        If forwarding fails, we still accept the job locally and mark status accordingly.
        """
        # Record as queued first
        self.job_store.upsert(job_id, state="queued", detail=None)

        # Best-effort submit to a common ComfyUI endpoint
        accepted = True
        detail: str | None = None
        try:
            url = f"{self.base_url}/prompt"
            payload = workflow
            resp = await self._client.post(url, json=payload)
            if resp.status_code >= 400:
                accepted = False
                detail = f"ComfyUI rejected workflow (HTTP {resp.status_code})"
                self.job_store.upsert(job_id, state="failed", detail=detail)
            else:
                detail = "Forwarded to ComfyUI"
                self.job_store.upsert(job_id, state="submitted", detail=detail)
        except Exception as e:
            # Network or other error; keep job but note degraded state
            accepted = True
            detail = f"Stored locally; forwarding failed: {e.__class__.__name__}"
            self.job_store.upsert(job_id, state="submitted", detail=detail)

        return accepted, detail

