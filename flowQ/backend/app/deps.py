import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import tx
from .permissions import label
from .security import decode_token

bearer = HTTPBearer(auto_error=False, description="Paste the access_token returned by POST /api/auth/login")

USER_SQL = """
select u.id, u.full_name, u.email, u.phone, u.role, u.department_id, u.active, u.archived,
       d.name as department_name, d.place_id, p.slug as place_slug,
       (u.role = 'admin' or exists (select 1 from role_permissions rp
                                    where rp.role = u.role and rp.permission = %s)) as allowed
from users u
left join departments d on d.id = u.department_id
left join places p on p.id = d.place_id
where u.id = %s
"""


def _load(creds: HTTPAuthorizationCredentials | None, perm: str | None = None, required: bool = True) -> dict | None:
    if creds is None or not creds.credentials:
        if required:
            raise HTTPException(401, "Please log in to continue.")
        return None
    try:
        payload = decode_token(creds.credentials)
        uid = str(uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        if required:
            raise HTTPException(401, "Your session has expired. Please log in again.")
        return None
    with tx() as cur:
        user = cur.execute(USER_SQL, (perm or "", uid)).fetchone()
    if not user or user["archived"] or not user["active"]:
        if required:
            raise HTTPException(401, "This account is inactive or no longer exists.")
        return None
    if perm and not user["allowed"]:
        raise HTTPException(403, f"Your role is not allowed to: {label(perm).lower()}.")
    user["id"] = str(user["id"])
    return user


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    return _load(creds)


def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict | None:
    return _load(creds, required=False)


def require(perm: str):
    """Dependency: the logged-in user must hold `perm` (administrators hold every permission)."""

    def dep(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        return _load(creds, perm)

    dep.__name__ = f"require_{perm}"
    return dep


def department_scope(user: dict, department_id: int | None) -> int:
    """Department a department-level action applies to: admins choose one, everyone else uses their own."""
    if user["role"] == "admin":
        if department_id is None:
            raise HTTPException(400, "Choose a department (department_id) first.")
        return department_id
    if not user["department_id"]:
        raise HTTPException(400, "Your account is not linked to a department.")
    if department_id is not None and department_id != user["department_id"]:
        raise HTTPException(403, "You can only manage your own department.")
    return user["department_id"]
