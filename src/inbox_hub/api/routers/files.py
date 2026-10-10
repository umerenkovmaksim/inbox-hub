from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from inbox_hub.api.deps import get_container
from inbox_hub.container import Container

router = APIRouter()


@router.get("/files/{sha256}")
async def get_file(sha256: str, c: Container = Depends(get_container)):
    row = await c.feed.get_file(sha256)
    if row is None:
        raise HTTPException(status_code=404)
    path = c.store.resolve(row.local_path)
    if not path.exists():
        raise HTTPException(status_code=410)
    return FileResponse(
        path,
        media_type=row.mime or "application/octet-stream",
        filename=row.original_name or path.name,
    )
