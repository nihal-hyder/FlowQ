from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..db import tx
from ..deps import current_user
from ..permissions import PERMISSIONS
from ..queue import log, notify
from ..schemas import clean_email, clean_name, clean_password, clean_phone
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["Auth"])

HOME = {"customer": "index.html#portals", "staff": "staff.html", "manager": "manager.html", "admin": "admin.html"}


class RegisterIn(BaseModel):
    full_name: str
    email: str
    phone: str
    password: str

    @field_validator("full_name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)

    @field_validator("email")
    @classmethod
    def v_email(cls, v):
        return clean_email(v)

    @field_validator("phone")
    @classmethod
    def v_phone(cls, v):
        v = clean_phone(v)
        if v is None:
            raise ValueError("Enter a phone number with 10 to 15 digits")
        return v

    @field_validator("password")
    @classmethod
    def v_password(cls, v):
        return clean_password(v)


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def v_email(cls, v):
        return clean_email(v)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def v_password(cls, v):
        return clean_password(v)


class ForgotIn(BaseModel):
    email: str


def public_user(u: dict) -> dict:
    return {
        "id": str(u["id"]), "full_name": u["full_name"], "email": u["email"], "phone": u.get("phone"),
        "role": u["role"], "department_id": u.get("department_id"), "department": u.get("department_name"),
        "home": HOME[u["role"]],
    }


def permissions_for(cur, role: str) -> list[str]:
    if role == "admin":
        return list(PERMISSIONS)
    return [r["permission"] for r in cur.execute("select permission from role_permissions where role = %s", (role,))]


def _session(cur, u: dict) -> dict:
    return {"access_token": create_token(u["id"], u["role"]), "token_type": "bearer",
            "user": public_user(u) | {"permissions": permissions_for(cur, u["role"])}}


@router.post("/register", status_code=201, summary="Create a customer account")
def register(body: RegisterIn):
    pw_hash = hash_password(body.password)
    with tx() as cur:
        if cur.execute("select 1 from users where lower(email) = %s and not archived", (body.email,)).fetchone():
            raise HTTPException(409, "That email is already registered. Try logging in.")
        u = cur.execute(
            "insert into users (full_name, email, phone, password_hash, role) values (%s,%s,%s,%s,'customer') returning *",
            (body.full_name, body.email, body.phone, pw_hash),
        ).fetchone()
        log(cur, "Users", f"New customer account: {u['full_name']}", actor_id=u["id"])
        notify(cur, u["id"], "Welcome to flowQ", "Book an appointment or grab a walk-in token whenever you need one.")
        return _session(cur, u)


@router.post("/login", summary="Log in and receive a bearer token")
def login(body: LoginIn):
    with tx() as cur:
        u = cur.execute(
            "select u.*, d.name as department_name from users u left join departments d on d.id = u.department_id "
            "where lower(u.email) = %s and not u.archived",
            (body.email,),
        ).fetchone()
        if not u or not verify_password(body.password, u["password_hash"]):
            raise HTTPException(401, "Wrong email or password.")
        if not u["active"]:
            raise HTTPException(403, "This account has been deactivated. Contact your administrator.")
        return _session(cur, u)


@router.get("/me", summary="The logged-in user and their permissions")
def me(user: dict = Depends(current_user)):
    with tx() as cur:
        return public_user(user) | {"permissions": permissions_for(cur, user["role"])}


@router.post("/change-password")
def change_password(body: PasswordIn, user: dict = Depends(current_user)):
    with tx() as cur:
        row = cur.execute("select password_hash from users where id = %s", (user["id"],)).fetchone()
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(400, "Your current password is not correct.")
        cur.execute("update users set password_hash = %s where id = %s", (hash_password(body.new_password), user["id"]))
    return {"message": "Password updated."}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn):
    # No email provider is configured, so nothing is sent; the reply never reveals whether the email exists.
    return {"message": "If that email is registered, a reset link has been sent."}
