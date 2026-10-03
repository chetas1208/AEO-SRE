"""FastAPI dependencies: session, event bus, actor, pagination."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.events import EventBus, get_event_bus

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def event_bus_dep() -> EventBus:
    return get_event_bus()


BusDep = Annotated[EventBus, Depends(event_bus_dep)]


def actor_dep(x_actor: Annotated[str | None, Header()] = None) -> str:
    return (x_actor or "operator").strip()[:255] or "operator"


ActorDep = Annotated[str, Depends(actor_dep)]


@dataclass
class PageParams:
    limit: int = 50
    offset: int = 0


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]
