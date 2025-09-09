import argparse
import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(prog="cylindria", description="Cylindria reverse-proxy for ComfyUI")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host/IP to bind (default: 0.0.0.0)")
    args = parser.parse_args()

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

