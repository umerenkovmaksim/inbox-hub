import hmac

from fastapi import Depends, Header, HTTPException, Request

from inbox_hub.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


async def require_token(
    authorization: str | None = Header(default=None),
    c: Container = Depends(get_container),
) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    expected = c.settings.api.token.get_secret_value()
    if (
        not expected
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token.encode(), expected.encode())
    ):
        raise HTTPException(status_code=401)
