import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class Settings:
    comfyui_base_url: str = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key: str | None = os.getenv("CYLINDRIA_API_KEY")
    # Dev mode: save workflow JSONs before forwarding to ComfyUI
    dev_mode: bool = os.getenv("CYLINDRIA_DEV_MODE", "0").lower() in {"1", "true", "yes", "on"}
    dev_save_dir: Optional[str] = os.getenv("CYLINDRIA_DEV_SAVE_DIR") or str(Path("workflows_dev").resolve())


def get_settings() -> Settings:
    return Settings()
