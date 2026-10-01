from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from ..db import tx
from ..deps import optional_user
from ..queue import sweep
from ..schemas import clean_email, clean_name, clean_phone
from ..timeutil import today

router = APIRouter(prefix="/api", tags=["Public"])

ORG_STATS_SQL = """
select
  (select count(*) from appointments where appt_date = %(d)s and status <> 'Cancelled') as appointments_today,
  (select count(*) from tickets where queue_date = %(d)s and kind = 'walkin') as walkins_today,
  (select count(*) from tickets where queue_date = %(d)s and status = 'waiting') as waiting,
  (select count(*) from counters c join departments dd on dd.id = c.department_id
     where c.status in ('Available', 'Busy') and c.staff_id is not null and not dd.archived) as active_counters,
  (select coalesce(round(avg(extract(epoch from first_called_at - created_at)) / 60), 0)::int
     from tickets where queue_date = %(d)s and first_called_at is not null) as avg_wait
"""


def org_stats(cur) -> dict:
    return cur.execute(ORG_STATS_SQL, {"d": today()}).fetchone()


@router.get("/health")
def health():
    with tx() as cur:
        cur.execute("select 1")
    return {"status": "ok"}


@router.get("/catalog", summary="Places, their departments and services (active ones only)")
def catalog():
    with tx() as cur:
        places = cur.execute("select id, slug, name, description, icon from places order by sort_order, id").fetchall()
        depts = cur.execute(
            "select id, place_id, name, open_time, close_time, slot_minutes from departments "
            "where active and not archived order by name"
        ).fetchall()
        svcs = cur.execute(
            "select id, department_id, name, avg_minutes from services where active and not archived order by name"
        ).fetchall()
    by_dept: dict[int, list] = {}
    for s in svcs:
        by_dept.setdefault(s["department_id"], []).append(s)
    for d in depts:
        d["services"] = by_dept.get(d["id"], [])
        d["open_time"], d["close_time"] = d["open_time"].strftime("%H:%M"), d["close_time"].strftime("%H:%M")
    for p in places:
        p["departments"] = [d for d in depts if d["place_id"] == p["id"]]
    return places


@router.get("/public/stats", summary="Live numbers for the landing page")
def public_stats():
    with tx() as cur:
        sweep(cur)
        return org_stats(cur)


class FeedbackIn(BaseModel):
    name: str
    rating: int = Field(ge=1, le=5)
    comment: str = Field(min_length=10, max_length=600)
    context: str = Field(default="", max_length=80)

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)

    @field_validator("comment")
    @classmethod
    def v_comment(cls, v):
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Write at least 10 characters")
        return v


@router.get("/feedback")
def list_feedback():
    with tx() as cur:
        items = cur.execute(
            "select id, name, rating, comment, context, created_at from feedback order by created_at desc limit 24"
        ).fetchall()
        agg = cur.execute("select count(*) as count, coalesce(round(avg(rating), 1), 0)::float as average from feedback").fetchone()
    return agg | {"items": items}


@router.post("/feedback", status_code=201)
def add_feedback(body: FeedbackIn, user: dict | None = Depends(optional_user)):
    with tx() as cur:
        return cur.execute(
            "insert into feedback (user_id, name, rating, comment, context) values (%s,%s,%s,%s,%s) "
            "returning id, name, rating, comment, context, created_at",
            (user["id"] if user else None, body.name, body.rating, body.comment, body.context.strip()),
        ).fetchone()


class ContactIn(BaseModel):
    name: str
    email: str
    phone: str
    message: str = Field(max_length=2000)

    @field_validator("name")
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
            raise ValueError("Enter a valid phone number")
        return v

    @field_validator("message")
    @classmethod
    def v_msg(cls, v):
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Message needs at least 10 characters")
        return v


@router.post("/contact", status_code=201)
def contact(body: ContactIn):
    with tx() as cur:
        cur.execute("insert into contact_messages (name, email, phone, message) values (%s,%s,%s,%s)",
                    (body.name, body.email, body.phone, body.message))
    return {"message": f"Thanks {body.name}! We'll be in touch soon."}
