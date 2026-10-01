import re
from datetime import time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import tx
from ..deps import department_scope, require
from ..ops import (archive_service, create_service, hour_label, list_services, queue_length_by_hour, release_counter,
                   update_service)
from ..queue import eta_minutes, get_department, log, sweep
from ..schemas import clean_email, clean_name, clean_password, clean_phone
from ..security import hash_password
from ..timeutil import TZ, local_dt, minutes, now_local, slots_for, today

router = APIRouter(prefix="/api/manager", tags=["Department Manager"])

MAX_COUNTERS = 8
WORKLOAD_TARGET = 30   # tokens per staff member per day shown as 100 %


def _hours(dept: dict) -> tuple[int, int]:
    h0 = dept["open_time"].hour
    h1 = dept["close_time"].hour - (1 if dept["close_time"].minute == 0 else 0)
    return h0, max(h0, h1)


def _snapshot(cur, dep: int) -> dict:
    d = today()
    waiting = cur.execute("select t.token, s.avg_minutes from tickets t join services s on s.id = t.service_id "
                          "where t.department_id = %s and t.queue_date = %s and t.status = 'waiting' order by t.sort_at, t.id",
                          (dep, d)).fetchall()
    row = cur.execute(
        "select (select count(*) from counters where department_id = %(dep)s and status in ('Available', 'Busy') and staff_id is not null) as active,"
        "       (select count(*) from appointments where department_id = %(dep)s and appt_date = %(d)s and status <> 'Cancelled') as booked,"
        "       (select token from tickets where department_id = %(dep)s and queue_date = %(d)s and called_at is not null "
        "         order by called_at desc limit 1) as now_serving",
        {"dep": dep, "d": d}).fetchone()
    return {"waiting": waiting, "active": row["active"], "booked": row["booked"], "now_serving": row["now_serving"],
            "eta": eta_minutes(sum(w["avg_minutes"] for w in waiting), row["active"])}


# ------------------------------------------------------------------ live queue
@router.get("/overview", summary="Live queue: waiting, active counters, estimated wait, appointments today")
def overview(department_id: int | None = None, user=Depends(require("view_department_stats"))):
    with tx() as cur:
        sweep(cur)
        dep = department_scope(user, department_id)
        dept = get_department(cur, dep)
        snap = _snapshot(cur, dep)
        h0, h1 = _hours(dept)
        hourly = queue_length_by_hour(cur, today(), today(), h0, h1, dep)
        return {
            "department": {"id": dept["id"], "name": dept["name"], "place": dept["place_name"]},
            "waiting": len(snap["waiting"]), "active_counters": snap["active"], "eta_minutes": snap["eta"],
            "appointments_today": snap["booked"], "daily_limit": dept["daily_limit"], "now_serving": snap["now_serving"],
            "waiting_tokens": [w["token"] for w in snap["waiting"]],
            "hours": [{"label": hour_label(h), "value": hourly.get(h, 0)} for h in range(h0, h1 + 1)],
        }


@router.get("/departments", summary="Departments an admin can pick in the manager console")
def manager_departments(user=Depends(require("view_department_stats"))):
    with tx() as cur:
        if user["role"] == "admin":
            return cur.execute("select d.id, d.name, p.name as place from departments d join places p on p.id = d.place_id "
                               "where not d.archived order by p.sort_order, d.name").fetchall()
        return cur.execute("select d.id, d.name, p.name as place from departments d join places p on p.id = d.place_id "
                           "where d.id = %s", (user["department_id"],)).fetchall()


# ------------------------------------------------------------------ staff
class StaffIn(BaseModel):
    full_name: str
    email: str
    password: str
    phone: str | None = None

    @field_validator("full_name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)

    @field_validator("email")
    @classmethod
    def v_email(cls, v):
        return clean_email(v)

    @field_validator("password")
    @classmethod
    def v_password(cls, v):
        return clean_password(v)

    @field_validator("phone")
    @classmethod
    def v_phone(cls, v):
        return clean_phone(v)


def _staff_list(cur, dep: int) -> list[dict]:
    return cur.execute(
        "select u.id, u.full_name, u.email, u.phone, u.active, c.id as counter_id, c.name as counter, c.status as counter_status,"
        "       (select count(*) from tickets t where t.staff_id = u.id and t.queue_date = %s and t.status = 'completed') as served "
        "from users u left join counters c on c.staff_id = u.id "
        "where u.department_id = %s and u.role = 'staff' and not u.archived order by u.full_name",
        (today(), dep)).fetchall()


@router.get("/staff")
def list_staff(department_id: int | None = None, user=Depends(require("manage_staff"))):
    with tx() as cur:
        return _staff_list(cur, department_scope(user, department_id))


@router.post("/staff", status_code=201, summary="Add a staff member to the department")
def add_staff(body: StaffIn, department_id: int | None = None, user=Depends(require("manage_staff"))):
    pw = hash_password(body.password)
    with tx() as cur:
        dep = department_scope(user, department_id)
        dept = get_department(cur, dep)
        if cur.execute("select 1 from users where lower(email) = %s and not archived", (body.email,)).fetchone():
            raise HTTPException(409, "That email is already registered.")
        cur.execute("insert into users (full_name, email, phone, password_hash, role, department_id) "
                    "values (%s,%s,%s,%s,'staff',%s)", (body.full_name, body.email, body.phone, pw, dep))
        log(cur, "Users", f"Added {body.full_name} as Service Staff in {dept['name']}", actor_id=user["id"], department_id=dep)
        return _staff_list(cur, dep)


@router.delete("/staff/{staff_id}", summary="Remove a staff member (their counter closes)")
def remove_staff(staff_id: UUID, department_id: int | None = None, user=Depends(require("manage_staff"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        s = cur.execute("select * from users where id = %s and role = 'staff' and department_id = %s and not archived for update",
                        (staff_id, dep)).fetchone()
        if not s:
            raise HTTPException(404, "Staff member not found in this department.")
        counter = release_counter(cur, s["id"])
        cur.execute("update users set archived = true, active = false where id = %s", (s["id"],))
        log(cur, "Users", f"Removed staff member {s['full_name']}" + (f"; {counter} closed" if counter else ""),
            actor_id=user["id"], department_id=dep)
        return _staff_list(cur, dep)


# ------------------------------------------------------------------ counters
def _counters(cur, dep: int) -> dict:
    counters = cur.execute(
        "select c.id, c.name, c.status, c.staff_id, u.full_name as staff_name,"
        "       (select token from tickets t where t.counter_id = c.id and t.queue_date = %s and t.status in ('called', 'serving')) as current_token "
        "from counters c left join users u on u.id = c.staff_id where c.department_id = %s order by c.id",
        (today(), dep)).fetchall()
    staff = cur.execute("select id, full_name from users where department_id = %s and role = 'staff' and active and not archived "
                        "order by full_name", (dep,)).fetchall()
    return {"counters": counters, "staff": staff, "max_counters": MAX_COUNTERS}


def _counter(cur, counter_id: int, dep: int) -> dict:
    c = cur.execute("select * from counters where id = %s and department_id = %s for update", (counter_id, dep)).fetchone()
    if not c:
        raise HTTPException(404, "Counter not found.")
    return c


def _counter_busy(cur, counter_id: int) -> str | None:
    r = cur.execute("select token from tickets where counter_id = %s and queue_date = %s and status in ('called', 'serving')",
                    (counter_id, today())).fetchone()
    return r["token"] if r else None


@router.get("/counters")
def list_counters(department_id: int | None = None, user=Depends(require("manage_counters"))):
    with tx() as cur:
        return _counters(cur, department_scope(user, department_id))


@router.post("/counters", status_code=201)
def add_counter(department_id: int | None = None, user=Depends(require("manage_counters"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        dept = get_department(cur, dep, lock=True)
        names = [r["name"] for r in cur.execute("select name from counters where department_id = %s", (dep,))]
        if len(names) >= MAX_COUNTERS:
            raise HTTPException(409, f"You can have up to {MAX_COUNTERS} counters.")
        nums = [int(m.group()) for n in names if (m := re.search(r"\d+", n))]
        name = f"Counter {max(nums, default=0) + 1}"
        cur.execute("insert into counters (department_id, name, status) values (%s, %s, 'Closed')", (dep, name))
        log(cur, "Queue", f"{name} added to {dept['name']}", actor_id=user["id"], department_id=dep)
        return _counters(cur, dep)


@router.delete("/counters/{counter_id}")
def remove_counter(counter_id: int, department_id: int | None = None, user=Depends(require("manage_counters"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        c = _counter(cur, counter_id, dep)
        if tok := _counter_busy(cur, c["id"]):
            raise HTTPException(409, f"{c['name']} is serving {tok}. Wait until it is completed.")
        cur.execute("delete from counters where id = %s", (c["id"],))
        log(cur, "Queue", f"{c['name']} removed", actor_id=user["id"], department_id=dep)
        return _counters(cur, dep)


class CounterStatusIn(BaseModel):
    status: Literal["Active", "Break", "Closed"]


@router.put("/counters/{counter_id}/status")
def counter_status(counter_id: int, body: CounterStatusIn, department_id: int | None = None,
                   user=Depends(require("manage_counters"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        c = _counter(cur, counter_id, dep)
        new = "Available" if body.status == "Active" else body.status
        if body.status == "Active" and c["status"] in ("Available", "Busy"):
            return _counters(cur, dep)
        if body.status == "Active" and not c["staff_id"]:
            raise HTTPException(409, "Assign a staff member before opening this counter.")
        if body.status != "Active" and (tok := _counter_busy(cur, c["id"])):
            raise HTTPException(409, f"{c['name']} is serving {tok}. Wait until it is completed.")
        if new != c["status"]:
            cur.execute("update counters set status = %s where id = %s", (new, c["id"]))
            log(cur, "Queue", f"{c['name']} set to {body.status}", actor_id=user["id"], department_id=dep)
        return _counters(cur, dep)


class AssignIn(BaseModel):
    staff_id: UUID | None = None


@router.put("/counters/{counter_id}/staff", summary="Assign (or unassign with null) the staff member at a counter")
def assign_staff(counter_id: int, body: AssignIn, department_id: int | None = None, user=Depends(require("manage_counters"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        c = _counter(cur, counter_id, dep)
        if (str(c["staff_id"]) if c["staff_id"] else None) == (str(body.staff_id) if body.staff_id else None):
            return _counters(cur, dep)
        if tok := _counter_busy(cur, c["id"]):
            raise HTTPException(409, f"{c['name']} is serving {tok}. Wait until it is completed.")
        if body.staff_id is None:
            cur.execute("update counters set staff_id = null, status = 'Closed' where id = %s", (c["id"],))
            log(cur, "Queue", f"Staff unassigned; {c['name']} closed", actor_id=user["id"], department_id=dep)
            return _counters(cur, dep)
        s = cur.execute("select id, full_name from users where id = %s and department_id = %s and role = 'staff' "
                        "and active and not archived", (body.staff_id, dep)).fetchone()
        if not s:
            raise HTTPException(404, "Staff member not found in this department.")
        release_counter(cur, s["id"])                         # moving them from another counter
        cur.execute("update counters set staff_id = %s where id = %s", (s["id"], c["id"]))
        log(cur, "Queue", f"{s['full_name']} assigned to {c['name']}", actor_id=user["id"], department_id=dep)
        return _counters(cur, dep)


# ------------------------------------------------------------------ services
class ServiceIn(BaseModel):
    name: str
    avg_minutes: int = Field(ge=1, le=120)

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)


class ServicePatch(BaseModel):
    active: bool | None = None
    avg_minutes: int | None = Field(default=None, ge=1, le=120)


@router.get("/services")
def dept_services(department_id: int | None = None, user=Depends(require("manage_department_services"))):
    with tx() as cur:
        return list_services(cur, department_scope(user, department_id))


@router.post("/services", status_code=201)
def add_dept_service(body: ServiceIn, department_id: int | None = None, user=Depends(require("manage_department_services"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        create_service(cur, user, dep, body.name, body.avg_minutes)
        return list_services(cur, dep)


@router.patch("/services/{service_id}")
def patch_dept_service(service_id: int, body: ServicePatch, department_id: int | None = None,
                       user=Depends(require("manage_department_services"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        update_service(cur, user, service_id, dep, active=body.active, minutes=body.avg_minutes)
        return list_services(cur, dep)


@router.delete("/services/{service_id}")
def delete_dept_service(service_id: int, department_id: int | None = None, user=Depends(require("manage_department_services"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        archive_service(cur, user, service_id, dep)
        return list_services(cur, dep)


# ------------------------------------------------------------------ hours & limits
def _schedule(cur, dept: dict) -> dict:
    booked = cur.execute("select count(*) as n from appointments where department_id = %s and appt_date = %s "
                         "and status <> 'Cancelled'", (dept["id"], today())).fetchone()["n"]
    return {"open_time": dept["open_time"].strftime("%H:%M"), "close_time": dept["close_time"].strftime("%H:%M"),
            "slot_minutes": dept["slot_minutes"], "daily_limit": dept["daily_limit"], "slot_capacity": dept["slot_capacity"],
            "slots_per_day": len(slots_for(dept["open_time"], dept["close_time"], dept["slot_minutes"])),
            "booked_today": booked}


class ScheduleIn(BaseModel):
    open_time: time | None = None
    close_time: time | None = None
    slot_minutes: Literal[15, 30, 45, 60] | None = None
    daily_limit: int | None = Field(default=None, ge=10, le=500)
    slot_capacity: int | None = Field(default=None, ge=1, le=50)


@router.get("/schedule")
def get_schedule(department_id: int | None = None, user=Depends(require("manage_schedule"))):
    with tx() as cur:
        return _schedule(cur, get_department(cur, department_scope(user, department_id)))


@router.put("/schedule", summary="Set working hours, appointment duration, daily limit and per-slot capacity")
def put_schedule(body: ScheduleIn, department_id: int | None = None, user=Depends(require("manage_schedule"))):
    with tx() as cur:
        dep = department_scope(user, department_id)
        dept = get_department(cur, dep, lock=True)
        o = (body.open_time or dept["open_time"]).replace(second=0, microsecond=0)
        c = (body.close_time or dept["close_time"]).replace(second=0, microsecond=0)
        dur = body.slot_minutes or dept["slot_minutes"]
        if minutes(c) <= minutes(o):
            raise HTTPException(400, "Closing time must be after opening time.")
        if minutes(c) - minutes(o) < dur:
            raise HTTPException(400, "Working hours must fit at least one appointment.")
        cur.execute("update departments set open_time = %s, close_time = %s, slot_minutes = %s, "
                    "daily_limit = coalesce(%s, daily_limit), slot_capacity = coalesce(%s, slot_capacity) where id = %s",
                    (o, c, dur, body.daily_limit, body.slot_capacity, dep))
        log(cur, "Departments", f"{dept['name']} schedule updated: {o:%H:%M}–{c:%H:%M}, {dur}-min slots, "
                                f"limit {body.daily_limit or dept['daily_limit']}/day", actor_id=user["id"], department_id=dep)
        return _schedule(cur, get_department(cur, dep))


# ------------------------------------------------------------------ workload & waiting times
@router.get("/reports", summary="Staff workload and waiting-time statistics for today")
def reports(department_id: int | None = None, user=Depends(require("view_department_stats"))):
    with tx() as cur:
        sweep(cur)
        dep = department_scope(user, department_id)
        dept = get_department(cur, dep)
        d = today()
        staff = _staff_list(cur, dep)
        wait = cur.execute(
            "select coalesce(round(avg(extract(epoch from first_called_at - created_at)) / 60), 0)::int as avg_wait,"
            "       coalesce(round(max(extract(epoch from first_called_at - created_at)) / 60), 0)::int as longest "
            "from tickets where department_id = %s and queue_date = %s and first_called_at is not null", (dep, d)).fetchone()
        day_start = local_dt(d, time(0, 0))
        now = now_local()
        peak = cur.execute(
            "select coalesce(max(n), 0) as peak from (select (select count(*) from tickets tk where tk.department_id = %(dep)s "
            "  and tk.queue_date = %(d)s and tk.created_at <= pt.t "
            "  and coalesce(tk.first_called_at, tk.closed_at, 'infinity'::timestamptz) > pt.t) as n "
            "from generate_series(%(a)s::timestamptz, %(b)s::timestamptz, interval '10 minutes') pt(t)) x",
            {"dep": dep, "d": d, "a": day_start, "b": now}).fetchone()["peak"]
        buckets: dict[int, list[float]] = {}
        for r in cur.execute("select created_at, extract(epoch from first_called_at - created_at)::float / 60 as w "
                             "from tickets where department_id = %s and queue_date = %s and first_called_at is not null", (dep, d)):
            buckets.setdefault(r["created_at"].astimezone(TZ).hour, []).append(r["w"])
        by_hour = {h: round(sum(v) / len(v)) for h, v in buckets.items()}
        snap = _snapshot(cur, dep)
        h0, h1 = _hours(dept)
        return {
            "department": {"id": dept["id"], "name": dept["name"]},
            "target": WORKLOAD_TARGET,
            "workload": [{"id": str(s["id"]), "name": s["full_name"], "served": s["served"],
                          "percent": min(100, round(s["served"] / WORKLOAD_TARGET * 100)),
                          "counter": s["counter"], "counter_status": s["counter_status"]} for s in staff],
            "avg_wait": wait["avg_wait"], "longest_wait": wait["longest"], "peak_queue": max(peak, len(snap["waiting"])),
            "current_eta": snap["eta"],
            "wait_by_hour": [{"label": hour_label(h), "value": by_hour.get(h, 0)} for h in range(h0, h1 + 1)],
        }
