from __future__ import annotations

import asyncio
import contextlib
import json
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .jobs import JobStore


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

        event_token = payload.get("type") or payload.get("event") or "update"
        detail = str(event_token)
        event_lower = detail.lower()

        def clamp_percent(value: float | int | None) -> int | None:
            if not isinstance(value, (int, float)):
                return None
            numeric = float(value)
            if numeric != numeric or numeric in (float('inf'), float('-inf')):
                return None
            numeric = max(0.0, min(100.0, numeric))
            return int(round(numeric))

        progress_percent: int | None = None

        if event_lower == "progress":
            data = payload.get("data")
            if isinstance(data, dict):
                current = data.get("value")
                total = data.get("max")
                nodeId = data.get("node")
                try:
                    current_value = float(current)
                    total_value = float(total)
                    node_value = int(nodeId)
                except (TypeError, ValueError):
                    current_value = None
                    total_value = None
                else:
                    if (
                        current_value == current_value
                        and total_value == total_value
                        and total_value > 0
                    ):
                        capped_current = max(0.0, min(current_value, total_value))
                        computed_percent = (capped_current / total_value) * 100.0
                        if node_value==57:
                            progress_percent = computed_percent * 0.40
                        elif node_value==58:
                            progress_percent = computed_percent * 0.40 + 40
                        elif node_value==85:
                            progress_percent = computed_percent * 0.20 + 80
                        elif total_value < 10:
                            progress_percent = computed_percent * 0.50
                        
                        progress_percent = clamp_percent(progress_percent)

            detail = f"progress ({progress_percent}%)" if progress_percent is not None else "progress"

        new_state = job.state
        if progress_percent is not None:
            new_state = "completed" if progress_percent >= 100 else "running"
        else:
            lowered = detail.lower()
            if any(token in lowered for token in ("start", "running", "execute")):
                new_state = "running"
                # Track this prompt as the last running one for queue polling
                self._last_running_prompt = prompt_id
            elif any(token in lowered for token in ("complete", "finish", "done")):
                new_state = "completed"
                # Clear the last running prompt when completed
                if self._last_running_prompt == prompt_id:
                    self._last_running_prompt = None
            elif any(token in lowered for token in ("fail", "error", "abort")):
                new_state = "failed"
                # Clear the last running prompt on failure
                if self._last_running_prompt == prompt_id:
                    self._last_running_prompt = None

        self.job_store.upsert(
            job.job_id,
            state=new_state,
            detail=detail,
            prompt_id=prompt_id,
            progress=progress_percent,
            gpu_id=self.gpu_id,
        )

    async def ping(self) -> bool:
        try:
            resp = await self._client.get(f"{self.base_url}/")
            return resp.status_code < 500
        except Exception:
            return False

    async def submit_workflow(self, job_id: str, workflow: dict[str, Any]) -> Tuple[bool, str | None]:
        """Try to forward a workflow to ComfyUI."""
        self.job_store.upsert(job_id, state='queued', detail=None, gpu_id=self.gpu_id)

        if self.dev_save_dir is not None:
            try:
                self.dev_save_dir.mkdir(parents=True, exist_ok=True)
                out_path = self.dev_save_dir / f"{job_id}.json"
                out_path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass

        accepted = True
        detail: str | None = None
        prompt_id: str | None = None
        try:
            url = f"{self.base_url}/prompt"
            resp = await self._client.post(url, json=workflow)
            if resp.status_code >= 400:
                accepted = False
                detail = f"ComfyUI rejected workflow (HTTP {resp.status_code})"
                self.job_store.upsert(job_id, state='failed', detail=detail, gpu_id=self.gpu_id)
            else:
                prompt_id = self._extract_prompt_id(resp)
                detail = 'Forwarded to ComfyUI'
                self.job_store.upsert(
                    job_id,
                    state='submitted',
                    detail=detail,
                    prompt_id=prompt_id,
                    gpu_id=self.gpu_id,
                )
        except Exception as exc:
            accepted = True
            detail = f"Stored locally; forwarding failed: {exc.__class__.__name__}"
            self.job_store.upsert(job_id, state='submitted', detail=detail, gpu_id=self.gpu_id)

        return accepted, detail

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

            current_running_prompt = running[0] if running else None

            # Check if running prompt has changed
            if current_running_prompt != self._last_running_prompt:
                # If there was a previously running prompt and it's no longer running,
                # mark the job associated with it as completed
                if self._last_running_prompt:
                    job = self.job_store.find_by_prompt_id(self._last_running_prompt, gpu_id=self.gpu_id)
                    if job and job.state == "running":
                        self.job_store.upsert(
                            job.job_id,
                            state="completed",
                            detail="Completed via queue polling",
                            progress=100,
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

