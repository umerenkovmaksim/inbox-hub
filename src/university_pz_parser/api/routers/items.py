from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from university_pz_parser.api.deps import get_container
from university_pz_parser.api.schemas import (
    ItemOut,
    ItemsOut,
    ReceiveIn,
    ReceiveOut,
    item_out,
)
from university_pz_parser.container import Container
from university_pz_parser.enums import Source
from university_pz_parser.services.feed import FeedQuery

router = APIRouter()


@router.get("/items", response_model=ItemsOut)
async def list_items(
    date_from: datetime = Query(alias="from"),
    date_to: datetime = Query(alias="to"),
    source: Source | None = None,
    include_received: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    c: Container = Depends(get_container),
):
    if date_from.tzinfo is None or date_to.tzinfo is None:
        raise HTTPException(status_code=422, detail="from/to must include timezone")
    items = await c.feed.list_items(
        FeedQuery(date_from, date_to, source, include_received, limit)
    )
    out = [item_out(i, c.settings.feed_text_limit) for i in items]
    return ItemsOut(items=out, count=len(out))


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item_id: int, c: Container = Depends(get_container)):
    item = await c.feed.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404)
    return item_out(item, None)


@router.post("/items/receive", response_model=ReceiveOut)
async def receive(body: ReceiveIn, c: Container = Depends(get_container)):
    return ReceiveOut(updated=await c.feed.mark_received(body.ids, body.versions))
