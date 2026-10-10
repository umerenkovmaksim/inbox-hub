from fastapi import APIRouter, Depends, HTTPException

from inbox_hub.api.deps import get_container, require_admin
from inbox_hub.api.schemas import (
    CategoryIn,
    CategoryOut,
    ChannelOut,
    KeyIn,
    KeyOut,
    KeyTokenOut,
    TrackChatIn,
    category_out,
    channel_out,
    key_out,
)
from inbox_hub.container import Container
from inbox_hub.services.catalog import CatalogError, NotFoundError

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


async def _call(coro):
    try:
        return await coro
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/sync/{source}")
async def sync(source: str, c: Container = Depends(get_container)):
    job = c.jobs.get(source)
    if job is None:
        raise HTTPException(status_code=404)
    return (await c.runner.run(job)).as_dict()


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(c: Container = Depends(get_container)):
    return [category_out(x) for x in await c.catalog.list_categories()]


@router.post("/categories", response_model=CategoryOut)
async def create_category(body: CategoryIn, c: Container = Depends(get_container)):
    return category_out(
        await _call(
            c.catalog.create_category(
                body.slug, body.title, body.description, body.auto_sources
            )
        )
    )


@router.delete("/categories/{slug}")
async def delete_category(slug: str, c: Container = Depends(get_container)):
    await _call(c.catalog.delete_category(slug))
    return {"deleted": slug}


@router.put("/categories/{slug}/channels/{ref}", response_model=ChannelOut)
async def add_channel(slug: str, ref: str, c: Container = Depends(get_container)):
    return channel_out(await _call(c.catalog.add_channel(slug, ref)))


@router.delete("/categories/{slug}/channels/{ref}", response_model=ChannelOut)
async def remove_channel(slug: str, ref: str, c: Container = Depends(get_container)):
    return channel_out(await _call(c.catalog.remove_channel(slug, ref)))


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(c: Container = Depends(get_container)):
    return [channel_out(x) for x in await c.catalog.list_channels()]


@router.post("/telegram/chats", response_model=ChannelOut)
async def track_chat(body: TrackChatIn, c: Container = Depends(get_container)):
    channel = await _call(c.catalog.track_chat(body.chat_id, body.categories))
    await c.refresh_tracked_chats()
    return channel_out(channel)


@router.delete("/telegram/chats/{chat_id}", response_model=ChannelOut)
async def untrack_chat(chat_id: int, c: Container = Depends(get_container)):
    channel = await _call(c.catalog.untrack_chat(chat_id))
    await c.refresh_tracked_chats()
    return channel_out(channel)


@router.get("/telegram/dialogs")
async def telegram_dialogs(c: Container = Depends(get_container)):
    if c.tg is None or "telegram" not in c.jobs:
        raise HTTPException(status_code=503, detail="telegram is not running")
    return [
        {"id": d.id, "name": d.name}
        async for d in c.tg.client.iter_dialogs()
        if d.is_group or d.is_channel
    ]


@router.get("/keys", response_model=list[KeyOut])
async def list_keys(c: Container = Depends(get_container)):
    return [key_out(k) for k in await c.catalog.list_keys()]


@router.post("/keys", response_model=KeyTokenOut)
async def create_key(body: KeyIn, c: Container = Depends(get_container)):
    key, token = await _call(c.catalog.create_key(body.name, body.categories))
    return KeyTokenOut(name=key.name, token=token)


@router.post("/keys/{name}/rotate", response_model=KeyTokenOut)
async def rotate_key(name: str, c: Container = Depends(get_container)):
    return KeyTokenOut(name=name, token=await _call(c.catalog.rotate_key(name)))


@router.post("/keys/{name}/revoke")
async def revoke_key(name: str, c: Container = Depends(get_container)):
    await _call(c.catalog.revoke_key(name))
    return {"revoked": name}


@router.put("/keys/{name}/categories/{slug}", response_model=KeyOut)
async def grant(name: str, slug: str, c: Container = Depends(get_container)):
    return key_out(await _call(c.catalog.grant(name, slug)))


@router.delete("/keys/{name}/categories/{slug}", response_model=KeyOut)
async def ungrant(name: str, slug: str, c: Container = Depends(get_container)):
    return key_out(await _call(c.catalog.ungrant(name, slug)))
