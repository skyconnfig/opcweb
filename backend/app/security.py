import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings


PUBLIC_PATHS = {"/health", "/ready", "/docs", "/redoc", "/openapi.json"}


def is_authorized(request: Request) -> bool:
    expected = get_settings().api_auth_token
    if not expected or request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return True
    supplied = request.headers.get("Authorization", "")
    if request.url.path.endswith("/events/stream"):
        supplied = supplied or f"Bearer {request.query_params.get('access_token', '')}"
    token = supplied.removeprefix("Bearer ").strip()
    return bool(token) and secrets.compare_digest(token, expected)


async def auth_middleware(request: Request, call_next):
    if not is_authorized(request):
        return JSONResponse({"detail": "需要有效的 API Bearer Token"}, status_code=401)
    return await call_next(request)
