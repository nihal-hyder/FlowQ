"""Operations shared by the manager and admin consoles (services, staff/counter links, statistics)."""
from datetime import date, time, timedelta

from fastapi import HTTPException

from .queue import log
from .timeutil import local_dt, now_local, today

# ------------------------------------------------------------------ services
SERVICE_LIST_SQL = """
select s.id, s.name, s.avg_minutes, s.active, s.department_id, d.name as department
from services s join departments d on d.id = s.department_id
where not s.archived and not d.archived {extra}
order by d.name, s.name
"""


def list_services(cur, department_id: int | None = None) -> list[dict]:
    if department_id is None:
        return cur.execute(SERVICE_LIST_SQL.format(extra="")).fetchall()
    return cur.execute(SERVICE_LIST_SQL.format(extra="and s.department_id = %s"), (department_id,)).fetchall()


def create_service(cur, actor: dict, department_id: int, name: str, minutes: int) -> dict:
    dept = cur.execute("select id, name from departments where id = %s and not archived", (department_id,)).fetchone()
    if not dept:
        raise HTTPException(404, "Department not found.")
    if cur.execute("select 1 from services where department_id = %s and lower(name) = lower(%s) and not archived",
                   (department_id, name)).fetchone():
        raise HTTPException(409, f"{dept['name']} already has a service called {name}.")
    s = cur.execute("insert into services (department_id, name, avg_minutes) values (%s,%s,%s) returning id",
                    (department_id, name, minutes)).fetchone()
    log(cur, "Services", f"Added service {name} ({minutes} min) to {dept['name']}", actor_id=actor["id"], department_id=department_id)
    return s


def get_service_scoped(cur, service_id: int, department_id: int | None) -> dict:
    s = cur.execute("select s.*, d.name as department from services s join departments d on d.id = s.department_id "
                    "where s.id = %s and not s.archived for update of s", (service_id,)).fetchone()
    if not s or (department_id is not None and s["department_id"] != department_id):
        raise HTTPException(404, "Service not found.")
    return s


def update_service(cur, actor: dict, service_id: int, department_id: int | None, active=None, minutes=None, name=None):
    s = get_service_scoped(cur, service_id, department_id)
    if name is not None and name.lower() != s["name"].lower():
        if cur.execute("select 1 from services where department_id = %s and lower(name) = lower(%s) and not archived and id <> %s",
                       (s["department_id"], name, service_id)).fetchone():
            raise HTTPException(409, f"{s['department']} already has a service called {name}.")
    cur.execute("update services set active = coalesce(%s, active), avg_minutes = coalesce(%s, avg_minutes), "
                "name = coalesce(%s, name) where id = %s", (active, minutes, name, service_id))
    changes = []
    if active is not None and active != s["active"]:
        changes.append("available" if active else "unavailable")
    if minutes is not None and minutes != s["avg_minutes"]:
        changes.append(f"{minutes} min")
    if name is not None and name != s["name"]:
        changes.append(f"renamed to {name}")
    if changes:
        log(cur, "Services", f"Service {s['name']} ({s['department']}): {', '.join(changes)}", actor_id=actor["id"],
            department_id=s["department_id"])


def archive_service(cur, actor: dict, service_id: int, department_id: int | None):
    s = get_service_scoped(cur, service_id, department_id)
    cur.execute("update services set archived = true, active = false where id = %s", (service_id,))
    log(cur, "Services", f"Removed service {s['name']} from {s['department']}", actor_id=actor["id"], department_id=s["department_id"])


# ------------------------------------------------------------------ staff <-> counters
def release_counter(cur, staff_id) -> str | None:
    """Unassign a staff member from their counter (which closes). Refuses while they are serving someone."""
    c = cur.execute("select * from counters where staff_id = %s for update", (staff_id,)).fetchone()
    if not c:
        return None
    busy = cur.execute("select token from tickets where counter_id = %s and queue_date = %s and status in ('called', 'serving')",
                       (c["id"], today())).fetchone()
    if busy:
        raise HTTPException(409, f"This staff member is serving {busy['token']} at {c['name']}. Complete or skip it first.")
    cur.execute("update counters set staff_id = null, status = 'Closed' where id = %s", (c["id"],))
    return c["name"]


# ------------------------------------------------------------------ statistics
HOURLY_QUEUE_SQL = """
select x.h, avg(x.n)::float as value from (
  select p.h,
         (select count(*) from tickets tk
           where tk.queue_date = p.d {dep}
             and tk.created_at <= p.t
             and coalesce(tk.first_called_at, tk.closed_at, 'infinity'::timestamptz) > p.t) as n
  from unnest(%(ds)s::date[], %(hs)s::int[], %(ts)s::timestamptz[]) as p(d, h, t)
) x group by x.h order by x.h
"""


def queue_length_by_hour(cur, start: date, end: date, h0: int, h1: int, department_id: int | None = None) -> dict[int, float]:
    """Average number of people waiting at half past each hour (h0..h1) over the days start..end."""
    now = now_local()
    pts = []
    day = start
    while day <= end:
        for h in range(h0, h1 + 1):
            t = local_dt(day, time(h, 30))
            if t <= now:
                pts.append((day, h, t))
        day += timedelta(days=1)
    if not pts:
        return {}
    sql = HOURLY_QUEUE_SQL.format(dep="and tk.department_id = %(dep)s" if department_id else "")
    rows = cur.execute(sql, {"ds": [p[0] for p in pts], "hs": [p[1] for p in pts], "ts": [p[2] for p in pts],
                             "dep": department_id}).fetchall()
    return {r["h"]: round(r["value"], 1) for r in rows}


def hour_label(h: int) -> str:
    return str(h if h <= 12 else h - 12)
