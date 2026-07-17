from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure imports resolve when running from .../Lira/app
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from api.routes.heartbeat import router as heartbeat_router
from api.routes.speak import router as speak_router
from api.routes.memory import router as memory_router
from api.routes.phi import router as phi_router
from api.routes.xian import router as xian_router


def create_app() -> FastAPI:
    app = FastAPI(title="Lira API", version=os.getenv("LIRA_VERSION", "0.1"))
    # Local wallpaper is loaded via file://, so allow cross-origin reads from the local API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(heartbeat_router, prefix="/heartbeat", tags=["heartbeat"])
    app.include_router(speak_router, prefix="/speak", tags=["speak"])
    app.include_router(memory_router, prefix="/memory", tags=["memory"])
    app.include_router(phi_router, prefix="/phi", tags=["phi"])
    app.include_router(xian_router, prefix="/xian", tags=["xian"])

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()


# --- status routes (bus read-only)
from api.routes import status as status_routes
app.include_router(status_routes.router, prefix="/status")
