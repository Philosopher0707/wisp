"""Policy distribution router (M4 T4): minimal enterprise control plane.

Local bundle files remain the authority; this API is a distribution
convenience: publish (verify-then-hold), current (serve held bundle),
revoke (monotonic revocation_seq bump), health. Audit export arrives
with the evidence harness (M5).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER

logger = logging.getLogger(__name__)

router = APIRouter()


def _pubkey(request: Request) -> str:
    key = getattr(request.app.state, "policy_pubkey", None)
    if not key:
        raise HTTPException(status_code=503, detail="Policy distribution not configured")
    return key


class PublishRequest(BaseModel):
    payload: dict = Field(..., description="Bundle document (canonicalized server-side)")
    signature: str = Field(..., description="Detached base64 Ed25519 signature")


class RevokeRequest(BaseModel):
    revocation_seq: int = Field(..., ge=1, description="Must exceed the held sequence")


@router.post("/api/policy/publish", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def publish_policy(body: PublishRequest, request: Request):
    """Verify and hold a bundle. Tampered or rollback publishes → 422."""
    from wisp.policy.bundle import PolicyBundle, verify_bundle
    pubkey = _pubkey(request)
    try:
        bundle = PolicyBundle.from_dict(body.payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"malformed bundle: {e}")
    if not verify_bundle(bundle, body.signature, pubkey):
        raise HTTPException(status_code=422, detail="signature verification failed")
    held = getattr(request.app.state, "policy_bundle", None)
    if held is not None and bundle.revocation_seq < held.get("revocation_seq", 0):
        raise HTTPException(status_code=422, detail="stale revocation_seq (rollback refused)")
    request.app.state.policy_bundle = body.payload
    request.app.state.policy_signature = body.signature
    return {"ok": True, "org_id": bundle.org_id,
            "revocation_seq": bundle.revocation_seq}


@router.get("/api/policy/current", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def current_policy(request: Request):
    """Serve the held bundle (404 when none published yet)."""
    _pubkey(request)
    held = getattr(request.app.state, "policy_bundle", None)
    if held is None:
        raise HTTPException(status_code=404, detail="no bundle published")
    return {"bundle": held,
            "signature": getattr(request.app.state, "policy_signature", "")}


@router.post("/api/policy/revoke", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def revoke_policy(body: RevokeRequest, request: Request):
    """Emergency revocation: bump the held sequence (monotonic)."""
    _pubkey(request)
    held = getattr(request.app.state, "policy_bundle", None)
    if held is None:
        raise HTTPException(status_code=404, detail="no bundle published")
    if body.revocation_seq <= held.get("revocation_seq", 0):
        raise HTTPException(status_code=422, detail="revocation_seq must increase")
    held["revocation_seq"] = body.revocation_seq
    return {"ok": True, "revocation_seq": body.revocation_seq}


@router.get("/api/policy/health", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def policy_health(request: Request):
    """Distribution health: configured + held bundle summary."""
    configured = bool(getattr(request.app.state, "policy_pubkey", None))
    held = getattr(request.app.state, "policy_bundle", None)
    return {"configured": configured,
            "has_bundle": held is not None,
            "org_id": (held or {}).get("org_id", ""),
            "revocation_seq": (held or {}).get("revocation_seq", 0)}
