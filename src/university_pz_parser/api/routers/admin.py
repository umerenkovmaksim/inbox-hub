from fastapi import APIRouter, Depends, HTTPException

from university_pz_parser.api.deps import get_container
from university_pz_parser.container import Container

router = APIRouter(prefix="/admin")


@router.post("/sync/{source}")
async def sync(source: str, c: Container = Depends(get_container)):
    job = c.jobs.get(source)
    if job is None:
        raise HTTPException(status_code=404)
    return (await c.runner.run(job)).as_dict()
