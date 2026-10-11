from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from inbox_hub.api.deps import get_container, get_principal
from inbox_hub.api.schemas import (
    ItemOut,
    ItemsOut,
    ReceiveIn,
    ReceiveOut,
    item_out,
)
from inbox_hub.container import Container
from inbox_hub.enums import Source
from inbox_hub.services.access import Principal
from inbox_hub.services.feed import FeedQuery, NoReceiptsError, ScopeError

router = APIRouter()


@router.get("/items", response_model=ItemsOut)
async def list_items(
    date_from: datetime = Query(alias="from"),
    date_to: datetime = Query(alias="to"),
    source: Source | None = None,
    categories: list[str] = Query(default=[]),
    include_received: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    c: Container = Depends(get_container),
    p: Principal = Depends(get_principal),
):
    if date_from.tzinfo is None or date_to.tzinfo is None:
        raise HTTPException(status_code=422, detail="from/to must include timezone")
    query = FeedQuery(
        date_from, date_to, source, include_received, limit, tuple(categories)
    )
    try:
        entries = await c.feed.list_items(query, p)
    except ScopeError as exc:
        raise HTTPException(
            status_code=403, detail=f"categories not in scope: {exc}"
        ) from None
    out = [item_out(e, c.settings.feed_text_limit) for e in entries]
    return ItemsOut(items=out, count=len(out))


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: int,
    c: Container = Depends(get_container),
    p: Principal = Depends(get_principal),
):
    entry = await c.feed.get_item(item_id, p)
    if entry is None:
        raise HTTPException(status_code=404)
    return item_out(entry, None)


@router.post("/items/receive", response_model=ReceiveOut)
async def receive(
    body: ReceiveIn,
    c: Container = Depends(get_container),
    p: Principal = Depends(get_principal),
):
    try:
        updated = await c.feed.mark_received(body.ids, p, body.versions)
    except NoReceiptsError:
        raise HTTPException(
            status_code=400, detail="the admin token has no receipts; use an API key"
        ) from None
    return ReceiveOut(updated=updated)
