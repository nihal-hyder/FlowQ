"""Shared queue / appointment logic used by the customer, staff, manager and admin routers."""
import math
import threading
import time as _time
from datetime import time, timedelta

from fastapi import HTTPException
from psycopg import Cursor

from .timeutil import now_local, today

ACTIVE_APPT = ("Booked", "Confirmed", "Rescheduled", "Delayed")     # booked, not yet checked in
CHECKED_IN_APPT = ("Checked In", "Waiting", "In Service")
OPEN_TICKET = ("waiting", "called", "serving")
CHECKIN_EARLY_MIN = 60   # check-in opens this many minutes before the slot starts
GRACE_MIN = 30           # ...and closes this many minutes after the slot ends (then: Missed)

# ---------------------------------------------------------------- housekeeping
_sweep_lock = threading.Lock()
_last_sweep = 0.0

SWEEP_SQL = """
with old as (
  update tickets
     set status = case when status = 'serving' then 'completed' else 'no_show' end,
         closed_at = coalesce(closed_at, now())
   where queue_date < %(d)s and status in ('waiting', 'called', 'serving')
  returning appointment_id, status
), linked as (
  update appointments a
     set status = case when old.status = 'completed' then 'Completed' else 'Missed' end, updated_at = now()
    from old
   where old.appointment_id = a.id and a.status in ('Checked In', 'Waiting', 'In Service')
  returning a.id
)
update appointments
   set status = 'Missed', updated_at = now()
 where status in ('Booked', 'Confirmed', 'Rescheduled', 'Delayed')
   and (appt_date < %(d)s or (appt_date = %(d)s and slot_end < %(cutoff)s))
"""


def sweep(cur: Cursor, force: bool = False) -> None:
    """Close yesterday's open tokens and mark appointments nobody checked in for as Missed."""
    global _last_sweep
    with _sweep_lock:
        if not force and _time.monotonic() - _last_sweep < 30:
            return
        _last_sweep = _time.monotonic()
    now = now_local()
    cut = now - timedelta(minutes=GRACE_MIN)
    cutoff = cut.time() if cut.date() == now.date() else time(0, 0)
    cur.execute(SWEEP_SQL, {"d": now.date(), "cutoff": cutoff})


# ---------------------------------------------------------------- log & notify
def log(cur: Cursor, category: str, message: str, actor_id=None, department_id=None, ticket_id=None, event=None):
    cur.execute(
        "insert into activity_log (category, message, event, actor_id, department_id, ticket_id) values (%s,%s,%s,%s,%s,%s)",
        (category, message, event, actor_id, department_id, ticket_id),
    )


def notify(cur: Cursor, user_id, title: str, body: str = ""):
    if user_id:
        cur.execute("insert into notifications (user_id, title, body) values (%s,%s,%s)", (user_id, title, body))


NEAR_SQL = """
with nxt as (
  select id from tickets
   where department_id = %(dep)s and queue_date = %(d)s and status = 'waiting'
   order by sort_at, id limit 2
), upd as (
  update tickets t set near_notified = true
    from nxt where t.id = nxt.id and not t.near_notified and t.user_id is not null
  returning t.user_id, t.token
)
insert into notifications (user_id, title, body)
select user_id, token || ': your turn is close', 'Please head back to the counter area now.' from upd
"""


def notify_near(cur: Cursor, department_id: int):
    cur.execute(NEAR_SQL, {"dep": department_id, "d": today()})


# ---------------------------------------------------------------- lookups
def get_department(cur: Cursor, department_id: int, lock: bool = False) -> dict:
    row = cur.execute(
        "select d.*, p.name as place_name, p.slug as place_slug, p.appointment_prefix "
        "from departments d join places p on p.id = d.place_id where d.id = %s and not d.archived"
        + (" for update of d" if lock else ""),
        (department_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Department not found.")
    return row


def get_service(cur: Cursor, service_id: int) -> dict:
    row = cur.execute(
        "select s.*, d.name as department_name from services s join departments d on d.id = s.department_id "
        "where s.id = %s and not s.archived",
        (service_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Service not found.")
    return row


def require_open(dept: dict, service: dict | None = None):
    if not dept["active"]:
        raise HTTPException(409, f"{dept['name']} is not taking customers right now.")
    if service is not None and not service["active"]:
        raise HTTPException(409, f"{service['name']} is currently unavailable.")


# ---------------------------------------------------------------- tickets
def create_ticket(cur: Cursor, dept: dict, service: dict, user_id, kind: str, appointment_id=None) -> dict:
    d = today()
    n = cur.execute(
        "insert into token_counters (department_id, queue_date, last_number) values (%s, %s, 1) "
        "on conflict (department_id, queue_date) do update set last_number = token_counters.last_number + 1 "
        "returning last_number",
        (dept["id"], d),
    ).fetchone()["last_number"]
    token = f"{dept['token_prefix']}-{n:03d}"
    t = cur.execute(
        "insert into tickets (department_id, service_id, user_id, appointment_id, queue_date, number, token, kind, status) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,'waiting') returning *",
        (dept["id"], service["id"], user_id, appointment_id, d, n, token, kind),
    ).fetchone()
    what = "Walk-in token" if kind == "walkin" else "Appointment check-in, token"
    log(cur, "Queue", f"{what} {token} issued ({dept['name']} · {service['name']})",
        actor_id=user_id, department_id=dept["id"], ticket_id=t["id"], event="issued")
    return t


QUEUE_INFO_SQL = """
select
  (select count(*) from tickets w
    where w.department_id = %(dep)s and w.queue_date = %(d)s and w.status = 'waiting'
      and (w.sort_at, w.id) < (%(sort_at)s, %(id)s)) as ahead,
  (select coalesce(sum(s.avg_minutes), 0) from tickets w join services s on s.id = w.service_id
    where w.department_id = %(dep)s and w.queue_date = %(d)s and w.status = 'waiting'
      and (w.sort_at, w.id) < (%(sort_at)s, %(id)s)) as ahead_minutes,
  (select count(*) from counters c
    where c.department_id = %(dep)s and c.status in ('Available', 'Busy') and c.staff_id is not null) as active_counters,
  (select token from tickets n
    where n.department_id = %(dep)s and n.queue_date = %(d)s and n.called_at is not null
    order by n.called_at desc limit 1) as now_serving
"""

TICKET_SQL = """
select t.*, s.name as service_name, s.avg_minutes, d.name as department_name, p.name as place_name, p.slug as place_slug
from tickets t join services s on s.id = t.service_id
join departments d on d.id = t.department_id join places p on p.id = d.place_id
"""


def eta_minutes(ahead_minutes: int, active_counters: int) -> int | None:
    if active_counters <= 0:
        return None
    return math.ceil(ahead_minutes / active_counters)


def ticket_view(cur: Cursor, t: dict) -> dict:
    """A ticket plus live position, people ahead, estimated wait and a friendly message."""
    info = {"ahead": 0, "ahead_minutes": 0, "active_counters": 0, "now_serving": None}
    if t["status"] in OPEN_TICKET:
        info = cur.execute(QUEUE_INFO_SQL, {"dep": t["department_id"], "d": t["queue_date"],
                                            "sort_at": t["sort_at"], "id": t["id"]}).fetchone()
    ahead = info["ahead"] if t["status"] == "waiting" else 0
    eta = eta_minutes(info["ahead_minutes"], info["active_counters"]) if t["status"] == "waiting" else 0
    counter = t["counter_name"] or "the counter"
    st = t["status"]
    if st == "waiting":
        if info["active_counters"] == 0:
            msg = "No counters are open right now. Your place in line is kept."
        elif ahead == 0:
            msg = "You are next. Head back to the counter."
        elif ahead <= 2:
            msg = "Your turn is close. Head to the counter now."
        else:
            msg = "You can wait somewhere nearby instead of standing in line."
    elif st == "called":
        msg = f"Your token is called. Please go to {counter}."
    elif st == "serving":
        msg = f"You are being served at {counter}."
    elif st == "completed":
        msg = "Service completed. Thank you for visiting!"
    elif st == "cancelled":
        msg = "You left the queue."
    else:
        msg = "You missed your turn. Please get a new token if you still need help."
    return {
        "id": str(t["id"]), "token": t["token"], "status": st, "kind": t["kind"],
        "department_id": t["department_id"], "department": t.get("department_name"),
        "place": t.get("place_name"), "place_slug": t.get("place_slug"),
        "service_id": t["service_id"], "service": t.get("service_name"),
        "appointment_id": str(t["appointment_id"]) if t["appointment_id"] else None,
        "counter": t["counter_name"], "position": ahead + 1 if st == "waiting" else 0, "ahead": ahead,
        "eta_minutes": eta, "active_counters": info["active_counters"], "now_serving": info["now_serving"],
        "skip_count": t["skip_count"], "created_at": t["created_at"], "message": msg,
    }


def fetch_ticket(cur: Cursor, ticket_id: str, lock: bool = False) -> dict:
    row = cur.execute(TICKET_SQL + " where t.id = %s" + (" for update of t" if lock else ""), (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Token not found.")
    return row
