from fastapi import Depends, Header, HTTPException, status

from .config import get_settings, Settings


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> str | None:
    """
    If CYLINDRIA_API_KEY is set in the environment, enforce X-API-Key header.
    If not set, allow requests without authentication.
    """
    if settings.api_key is None:
        return None
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    return x_api_key

