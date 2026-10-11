import logging

from fastapi import Depends, Header, HTTPException, Request

from inbox_hub.container import Container
from inbox_hub.services.access import Principal

log = logging.getLogger("inbox_hub.access")


def get_container(request: Request) -> Container:
    return request.app.state.container


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    c: Container = Depends(get_container),
) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    principal = None
    if scheme.lower() == "bearer":
        principal = await c.access.authenticate(token.strip())
    if principal is None:
        raise HTTPException(status_code=401)
    log.info(
        "%s %s %s %s",
        principal.name,
        request.method,
        request.url.path,
        request.url.query,
    )
    return principal


async def require_admin(p: Principal = Depends(get_principal)) -> Principal:
    if not p.is_admin:
        raise HTTPException(status_code=403)
    return p
