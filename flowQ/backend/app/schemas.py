"""Shared request-body validation helpers."""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[\d\s-]{10,15}$")


def clean_name(v: str) -> str:
    v = " ".join(str(v).split())
    if len(v) < 2:
        raise ValueError("Enter a name with at least 2 characters")
    if len(v) > 80:
        raise ValueError("Name is too long (80 characters max)")
    return v


def clean_email(v: str) -> str:
    v = str(v).strip().lower()
    if not EMAIL_RE.match(v) or len(v) > 254:
        raise ValueError("Enter a valid email, like name@example.com")
    return v


def clean_phone(v: str | None) -> str | None:
    if v is None or str(v).strip() == "":
        return None
    v = str(v).strip()
    if not PHONE_RE.match(v):
        raise ValueError("Enter a phone number with 10 to 15 digits")
    return v


def clean_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(v.encode()) > 72:
        raise ValueError("Password is too long (72 bytes max)")
    return v
