import argparse
import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(prog="cylindria", description="Cylindria reverse-proxy for ComfyUI")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host/IP to bind (default: 0.0.0.0)")
    parser.add_argument("--numberOfGpus", type=int, default=None, help="Number of ComfyUI instances (1-8, default from env or 1)")
    parser.add_argument("--dev", action="store_true", help="Enable dev mode: save workflows before forwarding")
    parser.add_argument("--dev-save-dir", type=str, default=None, help="Directory to save workflows in dev mode")
    args = parser.parse_args()

    # Start with env-based settings, then overlay CLI options
    from .config import get_settings, Settings

    settings = get_settings()
    if args.numberOfGpus is not None:
        settings.number_of_gpus = max(1, min(8, args.numberOfGpus))
    if args.dev:
        settings.dev_mode = True
    if args.dev_save_dir:
        settings.dev_save_dir = args.dev_save_dir

    app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
