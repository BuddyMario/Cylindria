import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _int_from_env(var_name: str, default: int) -> int:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    comfyui_base_url: str = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    number_of_gpus: int = _int_from_env("CYLINDRIA_NUM_GPUS", 1)
    api_key: str | None = os.getenv("CYLINDRIA_API_KEY")
    # Dev mode: save workflow JSONs before forwarding to ComfyUI
    dev_mode: bool = os.getenv("CYLINDRIA_DEV_MODE", "0").lower() in {"1", "true", "yes", "on"}
    dev_save_dir: Optional[str] = os.getenv("CYLINDRIA_DEV_SAVE_DIR") or str(Path("workflows_dev").resolve())

    def __post_init__(self) -> None:
        # Clamp GPU count to supported range (1-8)
        try:
            number = int(self.number_of_gpus)
        except (TypeError, ValueError):
            number = 1
        self.number_of_gpus = max(1, min(8, number))


def get_settings() -> Settings:
    return Settings()
