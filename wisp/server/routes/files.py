"""Files router.

Handles file operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/files", dependencies=[Depends(verify_api_key)])
async def list_files():
    return {"files": []}


@router.get("/api/files/tree", dependencies=[Depends(verify_api_key)])
async def file_tree():
    return {"tree": {}}


@router.post("/api/files", dependencies=[Depends(verify_api_key)])
async def create_file():
    return {"created": True}


@router.post("/api/files/edit", dependencies=[Depends(verify_api_key)])
async def edit_file():
    return {"edited": True}


@router.delete("/api/files", dependencies=[Depends(verify_api_key)])
async def delete_file():
    return {"deleted": True}
