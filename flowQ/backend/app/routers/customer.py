from datetime import date, time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..db import tx
from ..deps import require
from ..queue import (ACTIVE_APPT, CHECKED_IN_APPT, CHECKIN_EARLY_MIN, GRACE_MIN, OPEN_TICKET, TICKET_SQL,
                     create_ticket, fetch_ticket, get_department, get_service, log, notify, require_open, sweep,
                     ticket_view)
from ..timeutil import fmt_slot, local_dt, now_local, slots_for, today

router = APIRouter(prefix="/api", tags=["Customer"])

BOOKING_WINDOW_DAYS = 30
Reminder = Literal["Email", "SMS", "In-app alert"]


# ------------------------------------------------------------------ helpers
def availability_for(cur, dept: dict, d: date, exclude_id=None) -> dict:
    t0 = today()
    if d < t0 or d > t0 + timedelta(days=BOOKING_WINDOW_DAYS):
        raise HTTPException(400, f"Pick a date between today and {BOOKING_WINDOW_DAYS} days from now.")
    rows = [r for r in cur.execute(
        "select id, slot_start, slot_end from appointments where department_id = %s and appt_date = %s and status <> 'Cancelled'",
        (dept["id"], d)) if r["id"] != exclude_id]
    now = now_local()
    limit_reached = len(rows) >= dept["daily_limit"]
    slots = []
    for s, e in slots_for(dept["open_time"], dept["close_time"], dept["slot_minutes"]):
        booked = sum(1 for r in rows if r["slot_start"] < e and r["slot_end"] > s)
        past = d == t0 and s <= now.time()
        full = booked >= dept["slot_capacity"]
        slots.append({"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M"), "label": fmt_slot(s, e),
                      "booked": booked, "capacity": dept["slot_capacity"], "full": full, "past": past,
                      "available": dept["active"] and not (full or past or limit_reached)})
    return {"date": d.isoformat(), "department_id": dept["id"], "department": dept["name"], "active": dept["active"],
            "open_time": dept["open_time"].strftime("%H:%M"), "close_time": dept["close_time"].strftime("%H:%M"),
            "slot_minutes": dept["slot_minutes"], "daily_limit": dept["daily_limit"], "day_booked": len(rows),
            "limit_reached": limit_reached, "slots": slots}


def check_slot(cur, dept: dict, d: date, start: time, user_id: str, exclude_id=None) -> tuple[time, time]:
    av = availability_for(cur, dept, d, exclude_id)
    slot = next((s for s in av["slots"] if s["start"] == start.strftime("%H:%M")), None)
    if slot is None:
        raise HTTPException(400, "That time is not a valid slot for this department.")
    if slot["past"]:
        raise HTTPException(409, "That time slot has already started. Please pick a later one.")
    if av["limit_reached"]:
        raise HTTPException(409, "The daily appointment limit for that day has been reached. Please pick another day.")
    if slot["full"]:
        raise HTTPException(409, "That time slot is full. Please pick another one.")
    s, e = time.fromisoformat(slot["start"]), time.fromisoformat(slot["end"])
    clash = cur.execute(
        "select code from appointments where user_id = %s and appt_date = %s and id is distinct from %s "
        "and status = any(%s) and slot_start < %s and slot_end > %s",
        (user_id, d, exclude_id, list(ACTIVE_APPT + CHECKED_IN_APPT), e, s)).fetchone()
    if clash:
        raise HTTPException(409, f"You already have an appointment ({clash['code']}) at that time.")
    return s, e


APPT_SQL = """
select a.*, s.name as service_name, d.name as department_name, p.name as place_name, p.slug as place_slug
from appointments a join services s on s.id = a.service_id
join departments d on d.id = a.department_id join places p on p.id = d.place_id
"""


def checkin_window(a: dict):
    opens = local_dt(a["appt_date"], a["slot_start"]) - timedelta(minutes=CHECKIN_EARLY_MIN)
    closes = local_dt(a["appt_date"], a["slot_end"]) + timedelta(minutes=GRACE_MIN)
    return opens, closes


def appt_view(cur, a: dict) -> dict:
    ticket = cur.execute(TICKET_SQL + " where t.appointment_id = %s", (a["id"],)).fetchone()
    opens, closes = checkin_window(a)
    now = now_local()
    active = a["status"] in ACTIVE_APPT
    return {
        "id": str(a["id"]), "code": a["code"], "status": a["status"],
        "department_id": a["department_id"], "department": a["department_name"],
        "place": a["place_name"], "place_slug": a["place_slug"],
        "service_id": a["service_id"], "service": a["service_name"],
        "date": a["appt_date"].isoformat(), "slot_start": a["slot_start"].strftime("%H:%M"),
        "slot_end": a["slot_end"].strftime("%H:%M"), "slot_label": fmt_slot(a["slot_start"], a["slot_end"]),
        "reminder": a["reminder"], "checked_in_at": a["checked_in_at"], "created_at": a["created_at"],
        "can_cancel": active or (a["status"] in CHECKED_IN_APPT and ticket is not None and ticket["status"] == "waiting"),
        "can_reschedule": active,
        "can_check_in": active and opens <= now <= closes,
        "check_in_opens_at": opens.isoformat(),
        "ticket": ticket_view(cur, ticket) if ticket else None,
    }


def own_appointment(cur, appt_id: UUID, user: dict, lock: bool = False) -> dict:
    a = cur.execute(APPT_SQL + " where a.id = %s" + (" for update of a" if lock else ""), (appt_id,)).fetchone()
    if not a or (str(a["user_id"]) != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Appointment not found.")
    return a


def lock_user(cur, user_id: str):
    # serialises a customer's own concurrent requests (e.g. a double-clicked button)
    cur.execute("select 1 from users where id = %s for update", (user_id,))


# ------------------------------------------------------------------ appointments
@router.get("/departments/{department_id}/availability", summary="Appointment slots for a day")
def availability(department_id: int, day: date = Query(alias="date"), user=Depends(require("book_appointments"))):
    with tx() as cur:
        return availability_for(cur, get_department(cur, department_id), day)


class BookIn(BaseModel):
    service_id: int
    date: date
    slot_start: time
    reminder: Reminder | None = None


@router.post("/appointments", status_code=201, summary="Book an appointment")
def book(body: BookIn, user=Depends(require("book_appointments"))):
    with tx() as cur:
        lock_user(cur, user["id"])
        svc = get_service(cur, body.service_id)
        dept = get_department(cur, svc["department_id"], lock=True)   # lock: no over-booking under concurrency
        require_open(dept, svc)
        s, e = check_slot(cur, dept, body.date, body.slot_start, user["id"])
        seq = cur.execute("select nextval('appointment_code_seq') as n").fetchone()["n"]
        code = f"{dept['appointment_prefix']}-{seq:04d}"
        a = cur.execute(
            "insert into appointments (code, user_id, department_id, service_id, appt_date, slot_start, slot_end, status, reminder) "
            "values (%s,%s,%s,%s,%s,%s,%s,'Confirmed',%s) returning id",
            (code, user["id"], dept["id"], svc["id"], body.date, s, e, body.reminder)).fetchone()
        when = f"{body.date:%a %d %b} at {fmt_slot(s, e)}"
        notify(cur, user["id"], f"Appointment {code} confirmed", f"{dept['name']} · {svc['name']} on {when}.")
        log(cur, "Queue", f"Appointment booked: {dept['name']} · {svc['name']}, {when}", actor_id=user["id"], department_id=dept["id"])
        return appt_view(cur, own_appointment(cur, a["id"], user))


@router.get("/appointments/{appt_id}")
def get_appointment(appt_id: UUID, user=Depends(require("book_appointments"))):
    with tx() as cur:
        sweep(cur)
        return appt_view(cur, own_appointment(cur, appt_id, user))


class ReminderIn(BaseModel):
    reminder: Reminder


@router.patch("/appointments/{appt_id}/reminder", summary="Choose how to get your reminder")
def set_reminder(appt_id: UUID, body: ReminderIn, user=Depends(require("book_appointments"))):
    with tx() as cur:
        a = own_appointment(cur, appt_id, user, lock=True)
        if a["status"] not in ACTIVE_APPT:
            raise HTTPException(409, "Reminders can only be changed for upcoming appointments.")
        cur.execute("update appointments set reminder = %s, updated_at = now() where id = %s", (body.reminder, a["id"]))
        return appt_view(cur, own_appointment(cur, appt_id, user))


@router.post("/appointments/{appt_id}/cancel")
def cancel_appointment(appt_id: UUID, user=Depends(require("book_appointments"))):
    with tx() as cur:
        a = own_appointment(cur, appt_id, user, lock=True)
        t = cur.execute("select * from tickets where appointment_id = %s for update", (a["id"],)).fetchone()
        if a["status"] in CHECKED_IN_APPT:
            if not t or t["status"] != "waiting":
                raise HTTPException(409, "You are already being served, so this appointment can't be cancelled.")
            cur.execute("update tickets set status = 'cancelled', closed_at = now() where id = %s", (t["id"],))
        elif a["status"] not in ACTIVE_APPT:
            raise HTTPException(409, f"This appointment is {a['status'].lower()} and can't be cancelled.")
        cur.execute("update appointments set status = 'Cancelled', updated_at = now() where id = %s", (a["id"],))
        notify(cur, a["user_id"], f"Appointment {a['code']} cancelled", "The slot has been released for someone else.")
        log(cur, "Queue", f"Appointment {a['code']} cancelled ({a['department_name']})", actor_id=user["id"], department_id=a["department_id"])
        return appt_view(cur, own_appointment(cur, appt_id, user))


class RescheduleIn(BaseModel):
    date: date
    slot_start: time


@router.post("/appointments/{appt_id}/reschedule")
def reschedule(appt_id: UUID, body: RescheduleIn, user=Depends(require("book_appointments"))):
    with tx() as cur:
        lock_user(cur, user["id"])
        a = own_appointment(cur, appt_id, user, lock=True)
        if a["status"] not in ACTIVE_APPT:
            raise HTTPException(409, f"This appointment is {a['status'].lower()} and can't be rescheduled.")
        dept = get_department(cur, a["department_id"], lock=True)
        require_open(dept)
        s, e = check_slot(cur, dept, body.date, body.slot_start, str(a["user_id"]), exclude_id=a["id"])
        cur.execute("update appointments set appt_date = %s, slot_start = %s, slot_end = %s, status = 'Rescheduled', "
                    "updated_at = now() where id = %s", (body.date, s, e, a["id"]))
        when = f"{body.date:%a %d %b} at {fmt_slot(s, e)}"
        notify(cur, a["user_id"], f"Appointment {a['code']} rescheduled", f"New time: {when}.")
        log(cur, "Queue", f"Appointment {a['code']} rescheduled to {when}", actor_id=user["id"], department_id=dept["id"])
        return appt_view(cur, own_appointment(cur, appt_id, user))


@router.post("/appointments/{appt_id}/check-in", summary="Check in on arrival; you get a queue token")
def check_in(appt_id: UUID, user=Depends(require("book_appointments"))):
    too_late = False
    with tx() as cur:
        a = own_appointment(cur, appt_id, user, lock=True)
        if a["status"] in CHECKED_IN_APPT:
            raise HTTPException(409, "You are already checked in.")
        if a["status"] not in ACTIVE_APPT:
            raise HTTPException(409, f"This appointment is {a['status'].lower()}.")
        opens, closes = checkin_window(a)
        now = now_local()
        if now < opens:
            raise HTTPException(409, f"Check-in opens at {opens:%H:%M} on {opens:%a %d %b}.")
        if now > closes:
            # commit the Missed status first, then report the error (raising here would roll it back)
            cur.execute("update appointments set status = 'Missed', updated_at = now() where id = %s", (a["id"],))
            too_late = True
        else:
            dept = get_department(cur, a["department_id"])
            svc = get_service(cur, a["service_id"])
            t = create_ticket(cur, dept, svc, a["user_id"], "appointment", appointment_id=a["id"])
            cur.execute("update appointments set status = 'Waiting', checked_in_at = now(), updated_at = now() "
                        "where id = %s", (a["id"],))
            notify(cur, a["user_id"], f"Checked in · token {t['token']}",
                   f"You are in the {dept['name']} queue. We'll tell you when it's your turn.")
            return appt_view(cur, own_appointment(cur, appt_id, user))
    if too_late:
        raise HTTPException(409, "Check-in for this appointment has closed, so it was marked as missed.")


# ------------------------------------------------------------------ walk-in queue
class JoinIn(BaseModel):
    service_id: int


@router.post("/queue/join", status_code=201, summary="Join a department's digital queue (walk-in token)")
def join_queue(body: JoinIn, user=Depends(require("join_queue"))):
    with tx() as cur:
        lock_user(cur, user["id"])
        svc = get_service(cur, body.service_id)
        dept = get_department(cur, svc["department_id"])
        require_open(dept, svc)
        if settings.enforce_working_hours:
            now_t = now_local().time()
            if not (dept["open_time"] <= now_t < dept["close_time"]):
                raise HTTPException(409, f"{dept['name']} is open {dept['open_time']:%H:%M}–{dept['close_time']:%H:%M}. "
                                         "Please come back during working hours.")
        dup = cur.execute("select token from tickets where user_id = %s and department_id = %s and queue_date = %s "
                          "and status = any(%s)", (user["id"], dept["id"], today(), list(OPEN_TICKET))).fetchone()
        if dup:
            raise HTTPException(409, f"You already have token {dup['token']} in the {dept['name']} queue.")
        t = create_ticket(cur, dept, svc, user["id"], "walkin")
        notify(cur, user["id"], f"Your token is {t['token']}", f"{dept['name']} · {svc['name']}. Track your place live in flowQ.")
        return ticket_view(cur, fetch_ticket(cur, t["id"]))


@router.get("/queue/my", summary="Your open tokens today, with live position and wait")
def my_tickets(user=Depends(require("view_queue_position"))):
    with tx() as cur:
        sweep(cur)
        rows = cur.execute(TICKET_SQL + " where t.user_id = %s and t.queue_date = %s and t.status = any(%s) order by t.created_at",
                           (user["id"], today(), list(OPEN_TICKET))).fetchall()
        return [ticket_view(cur, t) for t in rows]


def own_ticket(cur, ticket_id: UUID, user: dict, lock=False) -> dict:
    t = fetch_ticket(cur, str(ticket_id), lock=lock)
    if str(t["user_id"]) != user["id"] and user["role"] != "admin":
        raise HTTPException(404, "Token not found.")
    return t


@router.get("/queue/tickets/{ticket_id}")
def get_ticket(ticket_id: UUID, user=Depends(require("view_queue_position"))):
    with tx() as cur:
        return ticket_view(cur, own_ticket(cur, ticket_id, user))


@router.post("/queue/tickets/{ticket_id}/cancel", summary="Leave the queue")
def leave_queue(ticket_id: UUID, user=Depends(require("join_queue"))):
    with tx() as cur:
        t = own_ticket(cur, ticket_id, user, lock=True)
        if t["status"] != "waiting":
            raise HTTPException(409, "You can only leave the queue while you are waiting.")
        cur.execute("update tickets set status = 'cancelled', closed_at = now() where id = %s", (t["id"],))
        if t["appointment_id"]:
            cur.execute("update appointments set status = 'Cancelled', updated_at = now() where id = %s", (t["appointment_id"],))
        log(cur, "Queue", f"{t['token']} left the queue ({t['department_name']})", actor_id=user["id"],
            department_id=t["department_id"], ticket_id=t["id"], event="left")
        return ticket_view(cur, fetch_ticket(cur, str(t["id"])))


# ------------------------------------------------------------------ history & notifications
TICKET_LABEL = {"waiting": "Waiting", "called": "Called", "serving": "In Service", "completed": "Completed",
                "cancelled": "Left queue", "no_show": "Missed"}


def tone(status: str) -> str:
    if status in ("Completed",):
        return "ok"
    if status in ("Cancelled", "Missed", "Left queue"):
        return "ba"
    return "wa"


@router.get("/me/history", summary="Your appointments and walk-in visits, newest first")
def history(user=Depends(require("view_history"))):
    with tx() as cur:
        sweep(cur)
        appts = cur.execute(APPT_SQL + " where a.user_id = %s order by a.appt_date desc, a.slot_start desc limit 60",
                            (user["id"],)).fetchall()
        walk = cur.execute(TICKET_SQL + " where t.user_id = %s and t.kind = 'walkin' order by t.created_at desc limit 60",
                           (user["id"],)).fetchall()
        now = now_local()
        items = []
        for a in appts:
            opens, closes = checkin_window(a)
            active = a["status"] in ACTIVE_APPT
            items.append({
                "type": "appointment", "id": str(a["id"]), "ref": a["code"],
                "title": f"{a['department_name']}: {a['service_name']}", "place": a["place_name"],
                "place_slug": a["place_slug"], "department_id": a["department_id"], "service_id": a["service_id"],
                "date": a["appt_date"].isoformat(), "time": fmt_slot(a["slot_start"], a["slot_end"]),
                "status": a["status"], "tone": tone(a["status"]),
                "sort": local_dt(a["appt_date"], a["slot_start"]).isoformat(),
                "can_cancel": active, "can_reschedule": active, "can_check_in": active and opens <= now <= closes,
            })
        for t in walk:
            label = TICKET_LABEL[t["status"]]
            items.append({
                "type": "walkin", "id": str(t["id"]), "ref": t["token"],
                "title": f"{t['department_name']}: {t['service_name']}", "place": t["place_name"],
                "place_slug": t["place_slug"], "department_id": t["department_id"], "service_id": t["service_id"],
                "date": t["queue_date"].isoformat(), "time": "Walk-in", "status": label, "tone": tone(label),
                "sort": t["created_at"].astimezone(now.tzinfo).isoformat(),
                "can_cancel": False, "can_reschedule": False, "can_check_in": False,
            })
        items.sort(key=lambda i: i["sort"], reverse=True)
        return items


@router.get("/notifications")
def notifications(user=Depends(require("receive_notifications"))):
    with tx() as cur:
        items = cur.execute("select id, title, body, read, created_at from notifications where user_id = %s "
                            "order by created_at desc, id desc limit 30", (user["id"],)).fetchall()
        unread = cur.execute("select count(*) as n from notifications where user_id = %s and not read",
                             (user["id"],)).fetchone()["n"]
    return {"unread": unread, "items": items}


@router.post("/notifications/read-all")
def read_all(user=Depends(require("receive_notifications"))):
    with tx() as cur:
        cur.execute("update notifications set read = true where user_id = %s and not read", (user["id"],))
    return {"unread": 0}


@router.post("/notifications/{notification_id}/read")
def read_one(notification_id: int, user=Depends(require("receive_notifications"))):
    with tx() as cur:
        cur.execute("update notifications set read = true where id = %s and user_id = %s", (notification_id, user["id"]))
    return {"ok": True}
