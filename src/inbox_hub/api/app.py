from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from inbox_hub.api.deps import get_principal
from inbox_hub.api.routers import admin, files, health, items
from inbox_hub.container import Container


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container is not None:
            app.state.container = container
            yield
            return
        from inbox_hub.runtime import run_server_lifespan

        async with run_server_lifespan(app):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if container is not None:
        app.state.container = container
    for module in (items, files, health, admin):
        # Safety net: no route is reachable without a valid key. Routes also take
        # the Principal themselves to scope their queries (resolved once per request).
        app.include_router(module.router, dependencies=[Depends(get_principal)])
    return app
