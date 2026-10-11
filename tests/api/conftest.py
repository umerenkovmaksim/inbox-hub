import httpx
import pytest

from inbox_hub.api.app import create_app
from inbox_hub.container import Container


@pytest.fixture
async def container(settings, engine):
    c = await Container.build(settings, with_sources=False)
    yield c
    await c.stop()


def client_for(container):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url="http://t"
    )
