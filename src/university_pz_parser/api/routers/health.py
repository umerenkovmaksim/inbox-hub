from fastapi import APIRouter, Depends

from university_pz_parser.api.deps import get_container
from university_pz_parser.api.schemas import HealthOut, health_out
from university_pz_parser.container import Container

router = APIRouter()


@router.get("/health", response_model=list[HealthOut])
async def health(c: Container = Depends(get_container)):
    return [health_out(r) for r in await c.health.snapshot()]
