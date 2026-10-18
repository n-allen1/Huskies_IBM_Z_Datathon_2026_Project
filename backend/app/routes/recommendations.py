from uuid import UUID

from fastapi import APIRouter, Depends

from ..auth import require_roles
from ..http import IdemKey, dump, get_services, idempotent, ok
from ..models import ReviewCreate, Role, User
from ..services import Services

router = APIRouter(prefix="/api/v1/recommendations")


@router.get("/{recommendationId}")
def get_recommendation(
    recommendationId: UUID,
    _=Depends(require_roles(Role.call_taker, Role.dispatcher, Role.supervisor, Role.system_admin)),
    services: Services = Depends(get_services),
):
    return ok(services.get_recommendation(recommendationId))


@router.post("/{recommendationId}/reviews")
def create_recommendation_review(
    recommendationId: UUID,
    body: ReviewCreate,
    idempotency_key: IdemKey,
    user: User = Depends(require_roles(Role.dispatcher, Role.supervisor)),
    services: Services = Depends(get_services),
):
    return idempotent(
        services, user, f"createRecommendationReview:{recommendationId}", idempotency_key, body,
        lambda: (201, dump(services.create_review(user, recommendationId, body))),
    )
