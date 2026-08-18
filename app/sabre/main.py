# main.py
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI

from db import init_db
from sabre_client import load_sabre_from_env, SabreConfigError
from app.core import config
from app.core.middleware import add_prod_guardrails
from app.api.routers.whatsapp import router as whatsapp_router
from app.api.routers.internal import router as internal_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    app.state.sabre = None
    app.state.http = httpx.AsyncClient(timeout=30)

    try:
        app.state.sabre = load_sabre_from_env()
        print(f"[Sabre] Loaded client. SABRE_ENV={config.SABRE_ENV}, READ_ONLY={config.SABRE_READ_ONLY}")
    except SabreConfigError as e:
        print(f"[Sabre] Not loaded (config error): {e}")
    except Exception as e:
        print(f"[Sabre] Not loaded (unexpected): {e}")

    yield

    await app.state.http.aclose()


def create_app() -> FastAPI:
    config.validate_required()
    app = FastAPI(lifespan=lifespan)

    add_prod_guardrails(app)
    app.include_router(whatsapp_router)
    app.include_router(internal_router)

    @app.get("/")
    def root():
        return {
            "status": "ok",
            "sabre_env": config.SABRE_ENV,
            "read_only": config.SABRE_READ_ONLY,
            "sabre_loaded": app.state.sabre is not None,
            "cache_ttl_seconds": config.BOOKING_CACHE_TTL_SECONDS,
        }

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()