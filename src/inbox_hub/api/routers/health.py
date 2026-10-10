from fastapi import APIRouter, Depends

from inbox_hub.api.deps import get_container, get_principal
from inbox_hub.api.schemas import HealthOut, MeOut, category_out, health_out
from inbox_hub.container import Container
from inbox_hub.services.access import Principal

router = APIRouter()


@router.get("/health", response_model=list[HealthOut], response_model_exclude_none=True)
async def health(
    c: Container = Depends(get_container),
    p: Principal = Depends(get_principal),
):
    return [health_out(r, detailed=p.is_admin) for r in await c.health.snapshot()]


@router.get("/me", response_model=MeOut)
async def me(
    c: Container = Depends(get_container),
    p: Principal = Depends(get_principal),
):
    return MeOut(
        name=p.name,
        admin=p.is_admin,
        categories=[category_out(x) for x in await c.catalog.visible_categories(p)],
    )
