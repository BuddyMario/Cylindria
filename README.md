# Cylindria

Minimal initial version of Cylindria: a small ASGI service (FastAPI + Uvicorn) intended to act as a reverse-proxy façade in front of one or more local ComfyUI instances (supports up to 8 GPUs per host, with each ComfyUI listening on consecutive ports).

This initial cut provides:

- HTTP endpoints:
  - `GET /serverstatus` – basic health/status, attempts to ping ComfyUI.
  - `PUT /startjob/{job_id}/` – accepts a workflow payload and forwards to ComfyUI (best-effort; stub-friendly).
  - `GET /jobstatus/{job_id}/` – returns the known status of a submitted job.

- CLI arguments:
  - `--port` to choose the Cylindria listening port (default `8000`).
  - `--numberOfGpus` to declare how many local ComfyUI instances/GPUs Cylindria should manage (default `1`, max `8`). GPU `n` is assumed to listen on `COMFYUI_BASE_URL` with its port incremented by `n`.
- Optional API-key protection via the `CYLINDRIA_API_KEY` environment variable.

> Note: The ComfyUI integration is intentionally conservative and aims to be easily adaptable. It uses the `COMFYUI_BASE_URL` environment variable (default `http://127.0.0.1:8000`). Many ComfyUI installations run at `http://127.0.0.1:8188`, so set `COMFYUI_BASE_URL` accordingly for your setup.

## Quickstart

1) Install dependencies (preferably in a virtualenv):

```
pip install -r requirements.txt
```

2) Configure (optional):

- `COMFYUI_BASE_URL` - base URL of your ComfyUI instance (default `http://127.0.0.1:8000`; many installs use `http://127.0.0.1:8188`). With multiple GPUs, Cylindria contacts GPU `n` at `base_port + n`.
- `CYLINDRIA_NUM_GPUS` - number of ComfyUI instances/GPUs running locally (default `1`, maximum `8`).
- `CYLINDRIA_API_KEY` - if set, requests must include header `X-API-Key: <value>`.

3) Run the server:

```
python -m cylindria --port 8000
```

4) (Optional) Run the desktop tester:

```
python cylindria_tester.py
```

## Endpoints

- `GET /serverstatus`
  - Query parameters: optional `GpuId` (default `0`, range `0..numberOfGpus-1`).
  - Returns `{ status: "ok" | "degraded", comfy_url: string, reachable: boolean, gpu_id: int }`.

- `PUT /startjob/{job_id}/`
  - Body: JSON object representing the workflow. Stored and forwarded to ComfyUI when possible.
  - Query parameters: optional `GpuId` (default `0`, range `0..numberOfGpus-1`).
  - Returns `{ job_id, accepted: boolean, detail, gpu_id }`.

- `GET /jobstatus/{job_id}/`
  - Returns the last known state for the job, e.g. `{ job_id, state, gpu_id, submitted_at, updated_at, progress, detail }`.
## Implementation Notes

- Cylindria keeps per-GPU background WebSocket listeners (plus queue polling) to ingest ComfyUI status events; the data is used to update stored job details.
- Networking to ComfyUI uses `httpx` with timeouts; failures do not crash the API and are surfaced in responses.
- A simple in-memory job store is used for initial tracking. This will reset on restart; you can swap this for a persistent store later.
- Security is API-key based and optional for a lightweight initial version.

## Dev Mode (Save Workflows)

Enable a development mode that saves incoming workflow JSONs to disk before forwarding to ComfyUI. Useful for debugging or inspecting payloads.

- Env vars:
  - `CYLINDRIA_DEV_MODE=1` — enables dev mode.
  - `CYLINDRIA_DEV_SAVE_DIR=/path/to/dir` — where to save files (defaults to `workflows_dev` under the current working directory).
- CLI flags:
  - `--dev` — enables dev mode.
  - `--dev-save-dir <dir>` — override save directory.

Examples:

```
CYLINDRIA_DEV_MODE=1 python -m cylindria --port 8000
```

```
python -m cylindria --port 8000 --dev --dev-save-dir ./workflows_dev
```

## Cylindria Tester (GUI)

The repo includes a small Tkinter-based desktop app to exercise the API:

- Launch: `python cylindria_tester.py`.
- Inputs: enter Cylindria URL (e.g. `http://127.0.0.1`), port (e.g. `8000`), and `Gpu Id` (default `0`).
- Buttons:
  - Server status: calls `GET /serverstatus` and displays the response.
  - Start Job: prompts you to select a JSON workflow file, generates a random job ID, calls `PUT /startjob/{job_id}/`, and shows the response. Stores the last job ID.
  - Job Status: prompts for a job ID (prefilled with the last used), calls `GET /jobstatus/{job_id}/`, and shows the response.

Notes:

- The tester uses `httpx` and standard-library Tkinter. On some Linux distros you may need to install Tk (e.g., `sudo apt-get install python3-tk`).
- If your Cylindria server enforces an API key (`CYLINDRIA_API_KEY`), the tester currently does not attach `X-API-Key`. You can temporarily disable auth or let us add header support in the tester.
