from fastapi import APIRouter, Depends, HTTPException

from inbox_hub.api.deps import get_container
from inbox_hub.container import Container

router = APIRouter(prefix="/admin")


@router.post("/sync/{source}")
async def sync(source: str, c: Container = Depends(get_container)):
    job = c.jobs.get(source)
    if job is None:
        raise HTTPException(status_code=404)
    return (await c.runner.run(job)).as_dict()
