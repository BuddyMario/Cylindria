# Cylindria

Minimal initial version of Cylindria: a small ASGI service (FastAPI + Uvicorn) intended to act as a reverse-proxy façade in front of a local ComfyUI instance.

This initial cut provides:

- HTTP endpoints:
  - `GET /serverstatus` – basic health/status, attempts to ping ComfyUI.
  - `PUT /startjob/{job_id}/` – accepts a workflow payload and forwards to ComfyUI (best-effort; stub-friendly).
  - `GET /jobstatus/{job_id}/` – returns the known status of a submitted job.
- CLI argument `--port` to choose the listening port (default `8000`).
- Optional API-key protection via the `CYLINDRIA_API_KEY` environment variable.

> Note: The ComfyUI integration is intentionally conservative and aims to be easily adaptable. By default it points at `http://127.0.0.1:8188`, which is the common ComfyUI default.

## Quickstart

1) Install dependencies (preferably in a virtualenv):

```
pip install -r requirements.txt
```

2) Configure (optional):

- `COMFYUI_BASE_URL` – base URL of your ComfyUI instance (default `http://127.0.0.1:8188`).
- `CYLINDRIA_API_KEY` – if set, requests must include header `X-API-Key: <value>`.

3) Run the server:

```
python -m cylindria --port 8000
```

## Endpoints

- `GET /serverstatus`
  - Returns `{ status: "ok" | "degraded", comfy_url: string, reachable: boolean }`.

- `PUT /startjob/{job_id}/`
  - Body: JSON object representing the workflow. Stored and forwarded to ComfyUI when possible.
  - Returns `{ job_id, accepted: boolean, detail }`.

- `GET /jobstatus/{job_id}/`
  - Returns the last known state for the job, e.g. `{ job_id, state, submitted_at, updated_at }`.

## Implementation Notes

- Networking to ComfyUI uses `httpx` with timeouts; failures do not crash the API and are surfaced in responses.
- A simple in-memory job store is used for initial tracking. This will reset on restart; you can swap this for a persistent store later.
- Security is API-key based and optional for a lightweight initial version.

