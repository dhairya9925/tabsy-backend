import json
import logging
import time
import uuid
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.rate_limiter import rate_limiter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def mobile_readiness_middleware(request: Request, call_next):
    """
    Mobile-ready middleware providing:
    - X-Request-ID correlation tracking across microservices/mobile logs.
    - Sliding window rate limiting with standard headers and 429 response envelope.
    - Structured JSON access logging with duration and client metadata.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    path = request.url.path
    is_exempt = path in ("/api/v1/health", "/openapi.json", f"{settings.API_V1_STR}/openapi.json") or path.startswith(("/docs", "/redoc"))
    rate_limit_headers = {}

    if settings.RATE_LIMIT_ENABLED and not is_exempt:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            client_key = f"auth:{hash(auth_header)}"
        else:
            client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "127.0.0.1")
            client_key = f"ip:{client_ip.split(',')[0].strip()}"

        rate_limiter.limit = settings.RATE_LIMIT_PER_MINUTE
        allowed, limit, remaining, retry_after = await rate_limiter.check(client_key)

        if not allowed:
            logger.warning(
                json.dumps({
                    "event": "rate_limit_exceeded",
                    "request_id": request_id,
                    "client_key": client_key,
                    "path": path,
                    "method": request.method,
                })
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "data": None,
                    "error": f"Rate limit exceeded. Please retry in {retry_after} seconds.",
                    "meta": {"retry_after": retry_after},
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                    "X-Request-ID": request_id,
                },
            )

        rate_limit_headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
        }

    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.error(
            json.dumps({
                "event": "request_failed",
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "duration_ms": duration_ms,
                "error": str(exc),
            })
        )
        raise exc

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    logger.info(
        json.dumps({
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": request.client.host if request.client else "unknown",
        })
    )

    response.headers["X-Request-ID"] = request_id
    for k, v in rate_limit_headers.items():
        response.headers[k] = v

    return response


@app.get("/openapi.json", include_in_schema=False)
async def get_root_openapi():
    """Return the OpenAPI schema at /openapi.json as well as /api/v1/openapi.json."""
    return JSONResponse(app.openapi())


# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers ensuring the standard ResponseEnvelope ({ data, error, meta }) on all errors
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": exc.detail, "meta": None},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    error_msg = "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"data": None, "error": f"Validation error: {error_msg}", "meta": {"details": errors}}, custom_encoder={ValueError: str}),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled server error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"data": None, "error": "Internal server error", "meta": None},
    )


# Mount API v1 router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", include_in_schema=False)
async def root_redirect():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "docs": "/docs",
        "api_v1": f"{settings.API_V1_STR}/health",
    }
