import os
from dataclasses import dataclass


@dataclass
class Settings:
    comfyui_base_url: str = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key: str | None = os.getenv("CYLINDRIA_API_KEY")


def get_settings() -> Settings:
    return Settings()

