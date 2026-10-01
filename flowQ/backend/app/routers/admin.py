import csv
import io
import string
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ..db import tx
from ..deps import require
from ..ops import archive_service, create_service, hour_label, list_services, queue_length_by_hour, release_counter, update_service
from ..permissions import ADMIN_ONLY, PERMISSIONS, ROLES
from ..queue import log, sweep
from .public import org_stats
from ..schemas import clean_email, clean_name, clean_password, clean_phone
from ..security import hash_password
from ..timeutil import today

router = APIRouter(prefix="/api/admin", tags=["Administrator"])

Role = Literal["customer", "staff", "manager", "admin"]


# ------------------------------------------------------------------ departments
def _departments(cur) -> dict:
    depts = cur.execute(
        "select d.id, d.name, d.active, d.token_prefix, d.place_id, p.name as place,"
        "       (select count(*) from services s where s.department_id = d.id and not s.archived) as services,"
        "       (select count(*) from users u where u.department_id = d.id and not u.archived and u.role in ('staff','manager')) as people "
        "from departments d join places p on p.id = d.place_id where not d.archived order by p.sort_order, d.name").fetchall()
    places = cur.execute("select id, name from places order by sort_order, id").fetchall()
    return {"departments": depts, "places": places}


class DeptIn(BaseModel):
    name: str
    place_id: int

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)


class DeptPatch(BaseModel):
    active: bool | None = None
    name: str | None = None

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return None if v is None else clean_name(v)


@router.get("/departments")
def list_departments(user=Depends(require("manage_departments"))):
    with tx() as cur:
        return _departments(cur)


@router.post("/departments", status_code=201)
def add_department(body: DeptIn, user=Depends(require("manage_departments"))):
    with tx() as cur:
        if not cur.execute("select 1 from places where id = %s", (body.place_id,)).fetchone():
            raise HTTPException(404, "Place not found.")
        cur.execute("select pg_advisory_xact_lock(4242, %s)", (body.place_id,))
        if cur.execute("select 1 from departments where place_id = %s and lower(name) = lower(%s) and not archived",
                       (body.place_id, body.name)).fetchone():
            raise HTTPException(409, "That department already exists.")
        used = {r["token_prefix"] for r in cur.execute(
            "select token_prefix from departments where place_id = %s and not archived", (body.place_id,))}
        first = body.name[0].upper()
        prefix = next((p for p in [first] + list(string.ascii_uppercase) if p.isalpha() and p not in used), "A")
        cur.execute("insert into departments (place_id, name, token_prefix) values (%s,%s,%s)", (body.place_id, body.name, prefix))
        log(cur, "Departments", f"Added department {body.name}", actor_id=user["id"])
        return _departments(cur)


@router.patch("/departments/{department_id}")
def patch_department(department_id: int, body: DeptPatch, user=Depends(require("manage_departments"))):
    with tx() as cur:
        d = cur.execute("select * from departments where id = %s and not archived for update", (department_id,)).fetchone()
        if not d:
            raise HTTPException(404, "Department not found.")
        if body.name and body.name.lower() != d["name"].lower() and cur.execute(
                "select 1 from departments where place_id = %s and lower(name) = lower(%s) and not archived",
                (d["place_id"], body.name)).fetchone():
            raise HTTPException(409, "That department already exists.")
        cur.execute("update departments set active = coalesce(%s, active), name = coalesce(%s, name) where id = %s",
                    (body.active, body.name, department_id))
        if body.active is not None and body.active != d["active"]:
            log(cur, "Departments", f"{d['name']} set to {'active' if body.active else 'inactive'}", actor_id=user["id"], department_id=d["id"])
        if body.name and body.name != d["name"]:
            log(cur, "Departments", f"{d['name']} renamed to {body.name}", actor_id=user["id"], department_id=d["id"])
        return _departments(cur)


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, user=Depends(require("manage_departments"))):
    with tx() as cur:
        d = cur.execute("select * from departments where id = %s and not archived for update", (department_id,)).fetchone()
        if not d:
            raise HTTPException(404, "Department not found.")
        if cur.execute("select 1 from services where department_id = %s and not archived", (department_id,)).fetchone():
            raise HTTPException(409, "Remove its services first.")
        if cur.execute("select 1 from users where department_id = %s and not archived and role in ('staff','manager')",
                       (department_id,)).fetchone():
            raise HTTPException(409, "Move or remove its staff and manager first.")
        if cur.execute("select 1 from tickets where department_id = %s and status in ('waiting','called','serving')",
                       (department_id,)).fetchone():
            raise HTTPException(409, "This department still has customers in its queue.")
        cur.execute("update departments set archived = true, active = false where id = %s", (department_id,))
        cur.execute("update counters set staff_id = null, status = 'Closed' where department_id = %s", (department_id,))
        log(cur, "Departments", f"Deleted department {d['name']}", actor_id=user["id"])
        return _departments(cur)


# ------------------------------------------------------------------ users
USERS_SQL = """
select u.id, u.full_name, u.email, u.phone, u.role, u.active, u.department_id, d.name as department, u.created_at
from users u left join departments d on d.id = u.department_id
where not u.archived {extra} order by case u.role when 'admin' then 0 when 'manager' then 1 when 'staff' then 2 else 3 end, u.full_name
"""


def _users(cur, role: str | None = None) -> list[dict]:
    if role:
        return cur.execute(USERS_SQL.format(extra="and u.role = %s"), (role,)).fetchall()
    return cur.execute(USERS_SQL.format(extra="")).fetchall()


class UserIn(BaseModel):
    full_name: str
    email: str
    password: str
    role: Role
    department_id: int | None = None
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


class UserPatch(BaseModel):
    role: Role | None = None
    department_id: int | None = None
    active: bool | None = None


def _check_department(cur, role: str, department_id: int | None) -> int | None:
    if role in ("staff", "manager"):
        if not department_id:
            raise HTTPException(400, f"Choose a department for this {ROLES[role]}.")
        if not cur.execute("select 1 from departments where id = %s and not archived", (department_id,)).fetchone():
            raise HTTPException(404, "Department not found.")
        return department_id
    return None


def _guard_admin_power(actor: dict, target_role: str | None, new_role: str | None = None):
    """Someone who was granted 'manage users' but is not an administrator can't create or change administrators."""
    if actor["role"] != "admin" and ("admin" in (target_role, new_role)):
        raise HTTPException(403, "Only an administrator can create or change administrator accounts.")


def _active_admins(cur) -> int:
    return len(cur.execute("select id from users where role = 'admin' and active and not archived for update").fetchall())


@router.get("/users")
def list_users(role: Role | None = None, user=Depends(require("manage_users"))):
    with tx() as cur:
        return _users(cur, role)


@router.post("/users", status_code=201)
def add_user(body: UserIn, user=Depends(require("manage_users"))):
    _guard_admin_power(user, body.role)
    pw = hash_password(body.password)
    with tx() as cur:
        dep = _check_department(cur, body.role, body.department_id)
        if cur.execute("select 1 from users where lower(email) = %s and not archived", (body.email,)).fetchone():
            raise HTTPException(409, "That email is already registered.")
        cur.execute("insert into users (full_name, email, phone, password_hash, role, department_id) values (%s,%s,%s,%s,%s,%s)",
                    (body.full_name, body.email, body.phone, pw, body.role, dep))
        log(cur, "Users", f"Added {body.full_name} as {ROLES[body.role]}", actor_id=user["id"], department_id=dep)
        return _users(cur)


@router.patch("/users/{user_id}", summary="Change a user's role, department or active status")
def patch_user(user_id: UUID, body: UserPatch, user=Depends(require("manage_users"))):
    with tx() as cur:
        t = cur.execute("select * from users where id = %s and not archived for update", (user_id,)).fetchone()
        if not t:
            raise HTTPException(404, "User not found.")
        _guard_admin_power(user, t["role"], body.role)
        is_self = str(t["id"]) == user["id"]
        role = body.role or t["role"]
        active = t["active"] if body.active is None else body.active
        if is_self and (role != t["role"] or not active):
            raise HTTPException(409, "You can't change your own role or deactivate yourself.")
        if t["role"] == "admin" and t["active"] and (role != "admin" or not active) and _active_admins(cur) < 2:
            raise HTTPException(409, "Keep at least one active administrator.")
        dep_in = body.department_id if "department_id" in body.model_fields_set else t["department_id"]
        dep = _check_department(cur, role, dep_in)
        if t["role"] == "staff" and (role != "staff" or not active or dep != t["department_id"]):
            release_counter(cur, t["id"])
        cur.execute("update users set role = %s, department_id = %s, active = %s where id = %s", (role, dep, active, t["id"]))
        if role != t["role"]:
            log(cur, "Users", f"{t['full_name']} is now {ROLES[role]}", actor_id=user["id"], department_id=dep)
        if active != t["active"]:
            log(cur, "Users", f"{t['full_name']} {'activated' if active else 'deactivated'}", actor_id=user["id"], department_id=dep)
        if role == t["role"] and dep != t["department_id"]:
            log(cur, "Users", f"{t['full_name']} moved to another department", actor_id=user["id"], department_id=dep)
        return _users(cur)


@router.delete("/users/{user_id}")
def remove_user(user_id: UUID, user=Depends(require("manage_users"))):
    with tx() as cur:
        t = cur.execute("select * from users where id = %s and not archived for update", (user_id,)).fetchone()
        if not t:
            raise HTTPException(404, "User not found.")
        _guard_admin_power(user, t["role"])
        if str(t["id"]) == user["id"]:
            raise HTTPException(409, "You can't remove your own account.")
        if t["role"] == "admin" and t["active"] and _active_admins(cur) < 2:
            raise HTTPException(409, "Keep at least one active administrator.")
        if t["role"] == "staff":
            release_counter(cur, t["id"])
        cur.execute("update tickets set status = 'cancelled', closed_at = now() where user_id = %s and status = 'waiting'", (t["id"],))
        cur.execute("update appointments set status = 'Cancelled', updated_at = now() where user_id = %s "
                    "and status in ('Booked','Confirmed','Rescheduled','Delayed')", (t["id"],))
        cur.execute("update users set archived = true, active = false where id = %s", (t["id"],))
        log(cur, "Users", f"Removed user {t['full_name']}", actor_id=user["id"])
        return _users(cur)


# ------------------------------------------------------------------ services
class AdminServiceIn(BaseModel):
    name: str
    department_id: int
    avg_minutes: int = Field(ge=1, le=120)

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return clean_name(v)


class AdminServicePatch(BaseModel):
    active: bool | None = None
    avg_minutes: int | None = Field(default=None, ge=1, le=120)
    name: str | None = None

    @field_validator("name")
    @classmethod
    def v_name(cls, v):
        return None if v is None else clean_name(v)


@router.get("/services")
def all_services(user=Depends(require("manage_services"))):
    with tx() as cur:
        return list_services(cur)


@router.post("/services", status_code=201)
def add_service(body: AdminServiceIn, user=Depends(require("manage_services"))):
    with tx() as cur:
        d = cur.execute("select active from departments where id = %s and not archived", (body.department_id,)).fetchone()
        if not d:
            raise HTTPException(404, "Department not found.")
        create_service(cur, user, body.department_id, body.name, body.avg_minutes)
        return list_services(cur)


@router.patch("/services/{service_id}")
def patch_service(service_id: int, body: AdminServicePatch, user=Depends(require("manage_services"))):
    with tx() as cur:
        update_service(cur, user, service_id, None, active=body.active, minutes=body.avg_minutes, name=body.name)
        return list_services(cur)


@router.delete("/services/{service_id}")
def delete_service(service_id: int, user=Depends(require("manage_services"))):
    with tx() as cur:
        archive_service(cur, user, service_id, None)
        return list_services(cur)


# ------------------------------------------------------------------ permissions
def _matrix(cur) -> dict:
    granted = {(r["role"], r["permission"]) for r in cur.execute("select role, permission from role_permissions")}
    return {
        "roles": [{"code": k, "label": v} for k, v in ROLES.items()],
        "permissions": [{"code": c, "label": lbl, "owner": owner, "admin_only": c in ADMIN_ONLY}
                        for c, (lbl, owner) in PERMISSIONS.items()],
        "matrix": {role: [p for p in PERMISSIONS if role == "admin" or (role, p) in granted] for role in ROLES},
    }


class PermIn(BaseModel):
    role: Role
    permission: str
    allowed: bool


@router.get("/permissions")
def get_permissions(user=Depends(require("manage_permissions"))):
    with tx() as cur:
        return _matrix(cur)


@router.put("/permissions", summary="Allow or remove one permission for a role")
def put_permission(body: PermIn, user=Depends(require("manage_permissions"))):
    if body.permission not in PERMISSIONS:
        raise HTTPException(400, "Unknown permission.")
    if body.role == "admin":
        raise HTTPException(409, "Administrator always keeps full access.")
    if body.allowed and body.permission in ADMIN_ONLY:
        raise HTTPException(409, "Only administrators can manage permissions.")
    with tx() as cur:
        if body.allowed:
            cur.execute("insert into role_permissions (role, permission) values (%s,%s) on conflict do nothing",
                        (body.role, body.permission))
        else:
            cur.execute("delete from role_permissions where role = %s and permission = %s", (body.role, body.permission))
        log(cur, "Permissions", f"{ROLES[body.role]}: {PERMISSIONS[body.permission][0]} {'allowed' if body.allowed else 'removed'}",
            actor_id=user["id"])
        return _matrix(cur)


# ------------------------------------------------------------------ activity
CATEGORIES = ("Departments", "Users", "Services", "Permissions", "Queue")


@router.get("/activity", summary="Organisation-wide live numbers and activity feed")
def activity(category: Literal["All", "Departments", "Users", "Services", "Permissions", "Queue"] = "All",
             user=Depends(require("view_org_activity"))):
    with tx() as cur:
        sweep(cur)
        stats = org_stats(cur)
        if category == "All":
            feed = cur.execute("select id, category, message, created_at from activity_log order by created_at desc, id desc limit 40").fetchall()
        else:
            feed = cur.execute("select id, category, message, created_at from activity_log where category = %s "
                               "order by created_at desc, id desc limit 40", (category,)).fetchall()
        return {"stats": stats, "feed": feed}


# ------------------------------------------------------------------ reports
RANGES = {"today": 1, "week": 7, "month": 30}


def _report(cur, rng: str) -> dict:
    sweep(cur)
    end = today()
    start = end - timedelta(days=RANGES[rng] - 1)
    t = cur.execute(
        "select count(*) filter (where status = 'completed') as served,"
        "       count(*) filter (where status in ('completed', 'no_show', 'cancelled')) as closed,"
        "       coalesce(round(avg(extract(epoch from first_called_at - created_at) / 60)), 0)::int as avg_wait "
        "from tickets where queue_date between %s and %s", (start, end)).fetchone()
    a = cur.execute(
        "select count(*) filter (where status = 'Missed') as missed, count(*) filter (where status in ('Completed', 'Missed')) as done "
        "from appointments where appt_date between %s and %s", (start, end)).fetchone()
    completion = round(t["served"] / t["closed"] * 100) if t["closed"] else 0
    no_show = round(a["missed"] / a["done"] * 100) if a["done"] else 0
    hourly = queue_length_by_hour(cur, start, end, 9, 16)
    depts = cur.execute(
        "select d.name, coalesce(round(avg(extract(epoch from t.first_called_at - t.created_at) / 60)), 0)::int as value "
        "from departments d left join tickets t on t.department_id = d.id and t.queue_date between %s and %s "
        "     and t.first_called_at is not null "
        "where not d.archived and d.active group by d.id, d.name order by d.name", (start, end)).fetchall()
    return {
        "range": rng, "from": start.isoformat(), "to": end.isoformat(),
        "metrics": [{"label": "Visitors served", "value": t["served"]},
                    {"label": "Completion rate", "value": f"{completion}%"},
                    {"label": "No-show rate", "value": f"{no_show}%"},
                    {"label": "Avg waiting time", "value": f"{t['avg_wait']} min"}],
        "hours": [{"hour": h, "label": f"{hour_label(h)}h", "value": hourly.get(h, 0)} for h in range(9, 17)],
        "departments": [{"label": r["name"], "value": r["value"]} for r in depts],
    }


@router.get("/reports", summary="Reports and analytics for today, the last 7 days or the last 30 days")
def reports(range: Literal["today", "week", "month"] = Query("today"), user=Depends(require("view_reports"))):
    with tx() as cur:
        return _report(cur, range)


@router.get("/reports.csv", summary="Download the report as CSV")
def reports_csv(range: Literal["today", "week", "month"] = Query("today"), user=Depends(require("view_reports"))):
    with tx() as cur:
        r = _report(cur, range)
        log(cur, "Queue", f"Report exported ({range})", actor_id=user["id"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["flowQ report", f"{r['from']} to {r['to']}"])
    w.writerow([])
    w.writerow(["Metric", "Value"])
    w.writerows([[m["label"], m["value"]] for m in r["metrics"]])
    w.writerow([])
    w.writerow(["Hour", "Average queue length"])
    w.writerows([[f"{h['hour']}:30", h["value"]] for h in r["hours"]])
    w.writerow([])
    w.writerow(["Department", "Average waiting time (min)"])
    w.writerows([[d["label"], d["value"]] for d in r["departments"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="flowQ-report-{range}.csv"'})
