from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.system import CapabilitiesOut
from app.services.capabilities import collect_capabilities

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities", response_model=CapabilitiesOut)
async def capabilities(
    session: SessionDep,
    probe: bool = Query(False, description="also test reachability of LLM/GitHub endpoints (network calls)"),
):
    return await collect_capabilities(session, probe=probe)
