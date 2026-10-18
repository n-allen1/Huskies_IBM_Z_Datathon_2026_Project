from typing import Annotated, Callable, Sequence

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .idempotency import fingerprint
from .models import User
from .services import Services

# Required on every operation the contract marks as harmful if repeated.
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def get_services(request: Request) -> Services:
    return request.app.state.services


def dump(model: BaseModel) -> dict:
    """Contract JSON: optional fields that are unset are omitted rather than sent as null."""
    return model.model_dump(mode="json", exclude_none=True)


def page_body(items: Sequence[BaseModel], page: dict) -> dict:
    """List envelope. `page.nextCursor` is required-but-nullable in the contract, so it is
    always present; other unset optional fields are omitted."""
    page = {k: v for k, v in page.items() if v is not None or k == "nextCursor"}
    return {"data": [dump(i) for i in items], "page": page}


def ok(model: BaseModel) -> JSONResponse:
    return JSONResponse(dump(model))


def created(model: BaseModel) -> JSONResponse:
    return JSONResponse(dump(model), status_code=201)


def idempotent(
    services: Services, user: User, operation: str, key: str, body: BaseModel, produce: Callable[[], tuple[int, dict]]
) -> JSONResponse:
    """Replay the original response for a repeated (user, operation, key); reject the key if the body differs."""
    status, payload = services.idempotency.run(
        user_id=user.userId, operation=operation, key=key,
        request_fingerprint=fingerprint(body.model_dump(mode="json")), produce=produce,
    )
    return JSONResponse(payload, status_code=status)
