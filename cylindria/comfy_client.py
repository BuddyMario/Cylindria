from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .jobs import JobStore


logger = logging.getLogger(__name__)


# Progress bands (start%, end%) for the slow nodes of base_workflow.json, in execution
# order. A band's node fills its band using its step counter. Nodes before the first
# band ramp progress from 0 to the first band's start by counting started nodes;
# all other nodes leave progress unchanged. 100% is only set on completion.
_PROGRESS_BANDS: dict[str, tuple[float, float]] = {
    "21": (15, 45),  # SamplerCustomAdvanced, first pass
    "16": (47, 80),  # SamplerCustomAdvanced, second pass (after latent upscale)
    "25": (80, 95),  # VAEDecode (reports no steps, so it holds at its band start)
    "41": (95, 99),  # VHS_VideoCombine
}
_PRE_BAND_END = min(start for start, _ in _PROGRESS_BANDS.values())


class ComfyClient:
    """Thin helper to talk to ComfyUI, with graceful fallbacks."""

    def __init__(
        self,
        base_url: str,
        job_store: JobStore,
        gpu_id: int = 0,
        dev_save_dir: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.job_store = job_store
        self.gpu_id = gpu_id
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0))
        self.dev_save_dir = Path(dev_save_dir) if dev_save_dir else None
        self._ws_url = self._build_ws_url(self.base_url)
        self._ws_task: asyncio.Task | None = None
        self._queue_poll_task: asyncio.Task | None = None
        self._listener_lock = asyncio.Lock()
        if self.dev_save_dir:
            self._ws_log_dir = self.dev_save_dir / f"ws_logs_gpu_{self.gpu_id}"
        else:
            self._ws_log_dir = None
        self._last_running_prompt: str | None = None
        # Per prompt_id: number of nodes in the submitted workflow, and nodes seen running.
        self._node_counts: dict[str, int] = {}
        self._started_nodes: dict[str, set[str]] = {}

    @staticmethod
    def _extract_prompt_id(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            prompt_id = payload.get('prompt_id')
            if isinstance(prompt_id, str):
                return prompt_id
        return None

    def _build_ws_url(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        path = parsed.path.rstrip('/')
        ws_path = f"{path}/ws" if path else '/ws'
        return urlunparse((scheme, parsed.netloc, ws_path, '', '', ''))


    def _open_ws_log(self):
        if self._ws_log_dir is None:
            return None
        try:
            self._ws_log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("ws_%Y%m%dT%H%M%S.%fZ.log")
            return (self._ws_log_dir / timestamp).open("a", encoding="utf-8")
        except Exception:
            return None

    def _log_ws_message(self, log_handle, payload):
        if log_handle is None:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "upstream",
        }
        if isinstance(payload, bytes):
            record["kind"] = "binary"
            record["payload_b64"] = base64.b64encode(payload).decode("ascii")
        else:
            record["kind"] = "text"
            record["payload"] = payload
        try:
            log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            log_handle.flush()
        except Exception:
            pass



    async def ensure_ws_listener(self) -> None:
        async with self._listener_lock:
            if self._ws_task and not self._ws_task.done():
                return
            self._ws_task = asyncio.create_task(self._ws_listener_loop())

            # Also start queue polling
            if self._queue_poll_task and not self._queue_poll_task.done():
                return
            self._queue_poll_task = asyncio.create_task(self._queue_polling_loop())

    async def stop_ws_listener(self) -> None:
        async with self._listener_lock:
            ws_task = self._ws_task
            self._ws_task = None
            queue_task = self._queue_poll_task
            self._queue_poll_task = None

        # Cancel WS task
        if ws_task:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

        # Cancel queue polling task
        if queue_task:
            queue_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_task

    async def _ws_listener_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=None, max_size=None) as ws:
                    backoff = 1.0
                    log_handle = self._open_ws_log()
                    try:
                        await self._consume_ws(ws, log_handle)
                    finally:
                        if log_handle is not None:
                            try:
                                log_handle.close()
                            except Exception:
                                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _consume_ws(self, ws, log_handle=None) -> None:
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    try:
                        decoded = message.decode('utf-8')
                    except Exception:
                        self._log_ws_message(log_handle, message)
                        continue
                    self._log_ws_message(log_handle, decoded)
                    message = decoded
                else:
                    self._log_ws_message(log_handle, message)
                await self._handle_ws_message(message)
        except ConnectionClosed:
            return

    async def _handle_ws_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        if not isinstance(payload, dict):
            return

        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            data = payload.get("data")
            if isinstance(data, dict):
                prompt_id = data.get("prompt_id") or data.get("id")
        if not isinstance(prompt_id, str):
            return

        job = self.job_store.find_by_prompt_id(prompt_id, gpu_id=self.gpu_id)
        if job is None:
            return

        if job.state in ("completed", "failed"):
            return

        event_type = str(payload.get("type") or payload.get("event") or "").lower()
        data = payload.get("data")
        if not isinstance(data, dict):
            return

        # Collect (node_id, value, max) for the nodes this event reports as running.
        running: list[tuple[str, Any, Any]] = []
        if event_type == "progress":
            running.append((str(data.get("node")), data.get("value"), data.get("max")))
        elif event_type == "progress_state":
            nodes = data.get("nodes")
            if isinstance(nodes, dict):
                for node_id, node in nodes.items():
                    if isinstance(node, dict) and node.get("state") == "running":
                        running.append((str(node_id), node.get("value"), node.get("max")))
        if not running:
            return

        started = self._started_nodes.setdefault(prompt_id, set())
        computed = 0.0
        for node_id, value, total in running:
            started.add(node_id)
            computed = max(computed, self._node_progress(prompt_id, node_id, value, total))

        # Progress never goes backwards; detail always has the same shape.
        progress = max(job.progress, min(99, int(computed)))
        self.job_store.upsert(
            job.job_id,
            state="running",
            detail=f"progress ({progress}%)",
            prompt_id=prompt_id,
            progress=progress,
            gpu_id=self.gpu_id,
        )

    def _node_progress(self, prompt_id: str, node_id: str, value: Any, total: Any) -> float:
        """Overall job progress implied by node_id running at step value/total."""
        band = _PROGRESS_BANDS.get(node_id)
        if band is None:
            # Before any band node starts, ramp toward the first band by started-node count.
            started = self._started_nodes.get(prompt_id, set())
            node_count = self._node_counts.get(prompt_id)
            if not node_count or started & _PROGRESS_BANDS.keys():
                return 0.0
            return _PRE_BAND_END * min(1.0, len(started) / node_count)

        start, end = band
        try:
            fraction = float(value) / float(total)
        except (TypeError, ValueError, ZeroDivisionError):
            return start
        if fraction != fraction:  # NaN
            return start
        return start + (end - start) * max(0.0, min(1.0, fraction))

    async def ping(self) -> bool:
        try:
            resp = await self._client.get(f"{self.base_url}/")
            return resp.status_code < 500
        except Exception:
            return False

    _SUBMIT_MAX_ATTEMPTS = 3
    _SUBMIT_RETRY_DELAY = 0.5  # seconds between retries

    async def submit_workflow(self, job_id: str, workflow: dict[str, Any]) -> Tuple[bool, str | None]:
        """Try to forward a workflow to ComfyUI, retrying on transient errors."""
        self.job_store.upsert(job_id, state='queued', detail=None, gpu_id=self.gpu_id)
        logger.info("Submitting workflow job_id=%s gpu_id=%s", job_id, self.gpu_id)

        if self.dev_save_dir is not None:
            try:
                self.dev_save_dir.mkdir(parents=True, exist_ok=True)
                out_path = self.dev_save_dir / f"{job_id}.json"
                out_path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass

        url = f"{self.base_url}/prompt"
        last_exc: Exception | None = None
        for attempt in range(1, self._SUBMIT_MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(url, json=workflow)
                logger.info(
                    "ComfyUI response job_id=%s gpu_id=%s attempt=%s status=%s body=%s",
                    job_id,
                    self.gpu_id,
                    attempt,
                    resp.status_code,
                    resp.text,
                )
                if resp.status_code >= 400:
                    # ComfyUI rejected the workflow — no point retrying
                    detail = f"ComfyUI rejected workflow (HTTP {resp.status_code})"
                    self.job_store.upsert(job_id, state='failed', detail=detail, gpu_id=self.gpu_id)
                    return False, detail

                prompt_id = self._extract_prompt_id(resp)
                nodes = workflow.get('prompt')
                if prompt_id and isinstance(nodes, dict):
                    self._node_counts[prompt_id] = len(nodes)
                detail = 'Forwarded to ComfyUI'
                self.job_store.upsert(
                    job_id,
                    state='submitted',
                    detail=detail,
                    prompt_id=prompt_id,
                    gpu_id=self.gpu_id,
                )
                return True, detail

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Submit attempt %s/%s failed job_id=%s gpu_id=%s: %s",
                    attempt,
                    self._SUBMIT_MAX_ATTEMPTS,
                    job_id,
                    self.gpu_id,
                    exc,
                )
                if attempt < self._SUBMIT_MAX_ATTEMPTS:
                    await asyncio.sleep(self._SUBMIT_RETRY_DELAY)

        detail = f"ComfyUI unreachable after {self._SUBMIT_MAX_ATTEMPTS} attempts: {last_exc!r}"
        self.job_store.upsert(job_id, state='failed', detail=detail, gpu_id=self.gpu_id)
        return False, detail

    async def _poll_queue(self) -> None:
        """Poll ComfyUI queue endpoint to check for job status updates."""
        try:
            resp = await self._client.get(f"{self.base_url}/queue")
            if resp.status_code != 200:
                return

            queue_data = resp.json()
            if not isinstance(queue_data, dict):
                return

            # Extract running prompt from queue data
            running = queue_data.get("queue_running", [])
            if not isinstance(running, list):
                running = []

            running_entry = running[0] if running else None
            current_running_prompt = running_entry[1] if isinstance(running_entry, (list, tuple)) and len(running_entry) > 1 else None

            # Check if running prompt has changed
            if current_running_prompt != self._last_running_prompt:
                # If there was a previously running prompt and it's no longer running,
                # mark the job associated with it as completed
                if self._last_running_prompt:
                    self._node_counts.pop(self._last_running_prompt, None)
                    self._started_nodes.pop(self._last_running_prompt, None)
                    job = self.job_store.find_by_prompt_id(self._last_running_prompt, gpu_id=self.gpu_id)
                    if job and job.state in ("running", "submitted"):
                        if (job.progress or 0) >= 80:
                            logger.warning(
                                "Queue poll: job_id=%s prompt_id=%s gpu_id=%s completed at %s%% progress",
                                job.job_id, self._last_running_prompt, self.gpu_id, job.progress or 0,
                            )
                            self.job_store.upsert(
                                job.job_id,
                                state="completed",
                                detail="Completed via queue polling",
                                progress=100,
                                gpu_id=self.gpu_id,
                            )
                        else:
                            logger.warning(
                                "Queue poll: job_id=%s prompt_id=%s gpu_id=%s disappeared from queue with only %s%% progress — marking failed",
                                job.job_id, self._last_running_prompt, self.gpu_id, job.progress or 0,
                            )
                            self.job_store.upsert(
                                job.job_id,
                                state="failed",
                                detail=f"Job disappeared from queue with only {job.progress or 0}% progress",
                                gpu_id=self.gpu_id,
                            )

                # Update the last running prompt
                self._last_running_prompt = current_running_prompt

        except Exception:
            # Silently ignore polling errors
            pass

    async def _queue_polling_loop(self) -> None:
        """Periodically poll the queue endpoint every 5 seconds."""
        while True:
            await self._poll_queue()
            await asyncio.sleep(5.0)

