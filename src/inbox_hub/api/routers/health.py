from fastapi import APIRouter, Depends

from inbox_hub.api.deps import get_container
from inbox_hub.api.schemas import HealthOut, health_out
from inbox_hub.container import Container

router = APIRouter()


@router.get("/health", response_model=list[HealthOut])
async def health(c: Container = Depends(get_container)):
    return [health_out(r) for r in await c.health.snapshot()]
