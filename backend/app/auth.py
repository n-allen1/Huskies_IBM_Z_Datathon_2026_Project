"""Fixed-token demo auth adapter.

The contract has no token endpoint: the demo adapter "issues tokens for fictional
users". Tokens here are static strings for fictional users and are NOT
production authentication. Swap `authenticate` for an OIDC validator later; the
role dependencies do not change.
"""

from fastapi import Depends, Header

from .config import DEMO_TENANT_ID, stable_id
from .errors import ApiError
from .models import AuthMode, ErrorCode, Role, User


def _user(token: str, name: str, *roles: Role) -> tuple[str, User]:
    return token, User(
        userId=stable_id(f"user:{token}"),
        displayName=name,
        tenantId=DEMO_TENANT_ID,
        roles=list(roles),
        authMode=AuthMode.demo_adapter,
    )


DEMO_USERS: dict[str, User] = dict(
    [
        _user("demo-call-taker", "Demo Call Taker", Role.call_taker),
        _user("demo-dispatcher", "Demo Dispatcher", Role.dispatcher),
        _user("demo-supervisor", "Demo Supervisor", Role.supervisor),
        _user("demo-hr", "Demo HR/Training Admin", Role.hr_training_admin),
        _user("demo-fleet", "Demo Fleet Admin", Role.fleet_equipment_admin),
        _user("demo-leadership", "Demo Department Leadership", Role.department_leadership),
        _user("demo-admin", "Demo System Admin", Role.system_admin),
    ]
)


def authenticate(authorization: str | None) -> User:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Missing or malformed bearer token.")
    user = DEMO_USERS.get(token.strip())
    if user is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Invalid bearer token.")
    return user


def current_user(authorization: str | None = Header(default=None)) -> User:
    return authenticate(authorization)


def require_roles(*roles: Role):
    """Dependency factory. The allowed set is exposed as `.allowed_roles` so tests
    can compare it with the "Allowed roles:" text in the contract."""
    allowed = frozenset(roles)

    def dependency(user: User = Depends(current_user)) -> User:
        if not allowed.intersection(user.roles):
            raise ApiError(ErrorCode.FORBIDDEN, "Your role is not permitted to perform this operation.")
        return user

    dependency.allowed_roles = allowed  # type: ignore[attr-defined]
    return dependency
