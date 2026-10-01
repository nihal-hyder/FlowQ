from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import tx
from ..deps import require
from ..queue import TICKET_SQL, fetch_ticket, log, notify, notify_near, sweep
from ..timeutil import fmt_slot, local_dt, today
from datetime import time

router = APIRouter(prefix="/api/staff", tags=["Service Staff"])

WAITING_SQL = """
select t.id, t.token, t.kind, t.status, t.skip_count, t.created_at, s.name as service_name,
       u.full_name as customer_name, a.slot_start, a.slot_end
from tickets t join services s on s.id = t.service_id
left join users u on u.id = t.user_id left join appointments a on a.id = t.appointment_id
where t.department_id = %s and t.queue_date = %s and t.status = 'waiting'
order by t.sort_at, t.id
"""


def my_counter(cur, user: dict, lock: bool = False) -> dict | None:
    return cur.execute(
        "select c.*, d.name as department_name from counters c join departments d on d.id = c.department_id "
        "where c.staff_id = %s" + (" for update of c" if lock else ""), (user["id"],)).fetchone()


def need_counter(cur, user: dict) -> dict:
    c = my_counter(cur, user, lock=True)
    if not c:
        raise HTTPException(409, "You are not assigned to a service counter yet. Ask your department manager.")
    return c


def current_ticket(cur, counter_id: int, lock: bool = False) -> dict | None:
    return cur.execute(TICKET_SQL + " where t.counter_id = %s and t.queue_date = %s and t.status in ('called', 'serving')"
                       + (" for update of t" if lock else ""), (counter_id, today())).fetchone()


def _row(t: dict) -> dict:
    return {"id": str(t["id"]), "token": t["token"], "name": t["customer_name"] or "Walk-in visitor",
            "service": t["service_name"], "type": "Appointment" if t["kind"] == "appointment" else "Walk-in",
            "skipped": t["skip_count"] > 0, "status": t["status"]}


def console(cur, user: dict) -> dict:
    sweep(cur)
    c = my_counter(cur, user)
    dept_id = c["department_id"] if c else user["department_id"]
    d = today()
    cur_t = current_ticket(cur, c["id"]) if c else None
    waiting = cur.execute(WAITING_SQL, (dept_id, d)).fetchall() if dept_id else []
    day_start = local_dt(d, time(0, 0))
    stats = cur.execute(
        "select (select count(*) from tickets where staff_id = %(u)s and queue_date = %(d)s and status = 'completed') as served,"
        "       (select count(*) from activity_log where actor_id = %(u)s and event = 'skipped' and created_at >= %(ds)s) as skipped",
        {"u": user["id"], "d": d, "ds": day_start}).fetchone()
    recent = cur.execute("select message, created_at from activity_log where actor_id = %s order by created_at desc, id desc limit 6",
                         (user["id"],)).fetchall()
    current = None
    if cur_t:
        u = cur.execute("select full_name from users where id = %s", (cur_t["user_id"],)).fetchone() if cur_t["user_id"] else None
        current = {"id": str(cur_t["id"]), "token": cur_t["token"], "status": cur_t["status"],
                   "name": u["full_name"] if u else "Walk-in visitor", "service": cur_t["service_name"],
                   "recalls": cur_t["recall_count"], "called_at": cur_t["called_at"], "started_at": cur_t["started_at"]}
    return {
        "staff": {"id": user["id"], "name": user["full_name"]},
        "department": user["department_name"] if not c else c["department_name"],
        "counter": {"id": c["id"], "name": c["name"], "status": c["status"]} if c else None,
        "current": current,
        "waiting": [_row(t) for t in waiting],
        "stats": {"waiting": len(waiting), "served_today": stats["served"], "skipped_today": stats["skipped"]},
        "recent": recent,
        "max_skips": settings.max_skips,
    }


@router.get("/console", summary="Everything the staff console shows: counter, current token, waiting customers")
def get_console(user=Depends(require("view_waiting_customers"))):
    with tx() as cur:
        return console(cur, user)


@router.get("/tickets/{ticket_id}", summary="View customer appointment details")
def ticket_details(ticket_id: UUID, user=Depends(require("view_customer_details"))):
    with tx() as cur:
        t = fetch_ticket(cur, str(ticket_id))
        if user["role"] != "admin" and t["department_id"] != user["department_id"]:
            raise HTTPException(404, "Token not found.")
        u = cur.execute("select full_name, phone, email from users where id = %s", (t["user_id"],)).fetchone() if t["user_id"] else None
        a = cur.execute("select code, slot_start, slot_end, appt_date, checked_in_at from appointments where id = %s",
                        (t["appointment_id"],)).fetchone() if t["appointment_id"] else None
        return {
            "id": str(t["id"]), "token": t["token"], "status": t["status"],
            "customer": u["full_name"] if u else "Walk-in visitor", "phone": (u or {}).get("phone") or "—",
            "email": (u or {}).get("email") or "—", "department": t["department_name"], "service": t["service_name"],
            "booking_type": "Appointment" if a else "Walk-in", "appointment_code": a["code"] if a else None,
            "appointment_time": fmt_slot(a["slot_start"], a["slot_end"]) if a else "—",
            "checked_in_at": a["checked_in_at"] if a else None, "joined_at": t["created_at"],
            "skip_count": t["skip_count"], "recall_count": t["recall_count"],
        }


@router.post("/call-next", summary="Call the next token")
def call_next(user=Depends(require("call_tokens"))):
    with tx() as cur:
        sweep(cur)
        c = need_counter(cur, user)
        if c["status"] != "Available":
            raise HTTPException(409, "Set your status to Available before calling the next token.")
        if current_ticket(cur, c["id"]):
            raise HTTPException(409, "Complete or skip the current token first.")
        nxt = cur.execute("select id from tickets where department_id = %s and queue_date = %s and status = 'waiting' "
                          "order by sort_at, id limit 1 for update skip locked", (c["department_id"], today())).fetchone()
        if not nxt:
            raise HTTPException(409, "No customers are waiting right now.")
        t = cur.execute(
            "update tickets set status = 'called', counter_id = %s, counter_name = %s, staff_id = %s, called_at = now(), "
            "first_called_at = coalesce(first_called_at, now()) where id = %s returning *",
            (c["id"], c["name"], user["id"], nxt["id"])).fetchone()
        notify(cur, t["user_id"], f"{t['token']}: it's your turn", f"Please go to {c['name']} now.")
        notify_near(cur, c["department_id"])
        log(cur, "Queue", f"{t['token']} called at {c['name']} ({c['department_name']})", actor_id=user["id"],
            department_id=c["department_id"], ticket_id=t["id"], event="called")
        return console(cur, user)


def _called(cur, user) -> tuple[dict, dict]:
    c = need_counter(cur, user)
    t = current_ticket(cur, c["id"], lock=True)
    if not t:
        raise HTTPException(409, "There is no token at your counter. Call the next token first.")
    return c, t


@router.post("/recall", summary="Recall the current token")
def recall(user=Depends(require("call_tokens"))):
    with tx() as cur:
        c, t = _called(cur, user)
        if t["status"] != "called":
            raise HTTPException(409, "Service has already started for this token.")
        cur.execute("update tickets set recall_count = recall_count + 1, called_at = now() where id = %s", (t["id"],))
        notify(cur, t["user_id"], f"{t['token']}: reminder, it's your turn", f"Please go to {c['name']} now.")
        log(cur, "Queue", f"{t['token']} recalled at {c['name']}", actor_id=user["id"], department_id=c["department_id"],
            ticket_id=t["id"], event="recalled")
        return console(cur, user)


@router.post("/skip", summary="Skip an unavailable customer (moves them to the end of the queue)")
def skip(user=Depends(require("call_tokens"))):
    with tx() as cur:
        c, t = _called(cur, user)
        if t["status"] != "called":
            raise HTTPException(409, "Service has already started for this token. Complete it instead.")
        skips = t["skip_count"] + 1
        if skips >= settings.max_skips:
            cur.execute("update tickets set status = 'no_show', skip_count = %s, closed_at = now() where id = %s", (skips, t["id"]))
            if t["appointment_id"]:
                cur.execute("update appointments set status = 'Missed', updated_at = now() where id = %s", (t["appointment_id"],))
            notify(cur, t["user_id"], f"{t['token']}: you missed your turn",
                   "You were called several times. Please get a new token if you still need help.")
            msg = f"Skipped {t['token']} {skips}×, marked as no-show"
        else:
            cur.execute("update tickets set status = 'waiting', skip_count = %s, sort_at = now(), counter_id = null, "
                        "counter_name = null, staff_id = null, called_at = null, near_notified = false where id = %s",
                        (skips, t["id"]))
            notify(cur, t["user_id"], f"{t['token']}: we missed you at the counter",
                   "You have been moved to the end of the queue. Stay close so you don't miss the next call.")
            msg = f"Skipped {t['token']} (unavailable), moved to end of queue"
        log(cur, "Queue", f"{msg} · {c['name']}", actor_id=user["id"], department_id=c["department_id"],
            ticket_id=t["id"], event="skipped")
        notify_near(cur, c["department_id"])
        return console(cur, user)


@router.post("/start", summary="Start service for the called token")
def start(user=Depends(require("serve_customers"))):
    with tx() as cur:
        c, t = _called(cur, user)
        if t["status"] != "called":
            raise HTTPException(409, "Service has already started.")
        cur.execute("update tickets set status = 'serving', started_at = now() where id = %s", (t["id"],))
        cur.execute("update counters set status = 'Busy' where id = %s", (c["id"],))
        if t["appointment_id"]:
            cur.execute("update appointments set status = 'In Service', updated_at = now() where id = %s", (t["appointment_id"],))
        log(cur, "Queue", f"Started service for {t['token']} at {c['name']}", actor_id=user["id"],
            department_id=c["department_id"], ticket_id=t["id"], event="started")
        return console(cur, user)


@router.post("/complete", summary="Complete service for the current token")
def complete(user=Depends(require("serve_customers"))):
    with tx() as cur:
        c, t = _called(cur, user)
        if t["status"] != "serving":
            raise HTTPException(409, "Start the service before completing it.")
        cur.execute("update tickets set status = 'completed', closed_at = now() where id = %s", (t["id"],))
        cur.execute("update counters set status = 'Available' where id = %s", (c["id"],))
        if t["appointment_id"]:
            cur.execute("update appointments set status = 'Completed', updated_at = now() where id = %s", (t["appointment_id"],))
        notify(cur, t["user_id"], f"{t['token']}: service completed", "Thanks for visiting. We'd love your feedback on flowQ.")
        log(cur, "Queue", f"{t['token']} completed at {c['name']} ({c['department_name']})", actor_id=user["id"],
            department_id=c["department_id"], ticket_id=t["id"], event="completed")
        return console(cur, user)


class StatusIn(BaseModel):
    status: Literal["Available", "Busy", "Break", "Closed"]


@router.put("/status", summary="Update service status (Available, Busy, Break, Closed)")
def set_status(body: StatusIn, user=Depends(require("update_service_status"))):
    with tx() as cur:
        c = need_counter(cur, user)
        if current_ticket(cur, c["id"]):
            raise HTTPException(409, "Complete or skip the current token first.")
        if c["status"] != body.status:
            cur.execute("update counters set status = %s where id = %s", (body.status, c["id"]))
            log(cur, "Queue", f"{c['name']} ({c['department_name']}) is now {body.status}", actor_id=user["id"],
                department_id=c["department_id"], event="status")
        return console(cur, user)
