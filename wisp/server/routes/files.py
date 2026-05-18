"""Files router.

Handles file operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/files")
async def list_files():
    return {"files": []}


@router.get("/api/files/tree")
async def file_tree():
    return {"tree": {}}


@router.post("/api/files")
async def create_file():
    return {"created": True}


@router.post("/api/files/edit")
async def edit_file():
    return {"edited": True}


@router.delete("/api/files")
async def delete_file():
    return {"deleted": True}
