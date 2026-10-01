"""End-to-end check of every role and permission against a running flowQ API.

Run it against a FRESHLY SEEDED database (it books, calls and edits things):
    python -m app.seed --reset
    python tests/smoke_test.py http://localhost:8000
"""
import sys
import uuid
from datetime import date, timedelta

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api"
PW = "flowq1234"
c = httpx.Client(timeout=30)
fails = 0


def check(name, cond, extra=""):
    global fails
    if not cond:
        fails += 1
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {extra}" if not cond and extra else ""))


def call(method, path, token=None, **kw):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return c.request(method, BASE + path, headers=h, **kw)


def login(email, pw=PW):
    r = call("POST", "/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, (email, r.text)
    return r.json()["access_token"]


def expect(name, r, code):
    check(f"{name} [{code}]", r.status_code == code, f"{r.status_code} {r.text[:200]}")
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


print("== public")
expect("health", call("GET", "/health"), 200)
cat = expect("catalog", call("GET", "/catalog"), 200)
check("3 places with departments", len(cat) == 3 and all(p["departments"] for p in cat))
SA = next(d for p in cat for d in p["departments"] if d["name"] == "Student Affairs")
REG = next(d for p in cat for d in p["departments"] if d["name"] == "Registrar")
expect("public stats", call("GET", "/public/stats"), 200)
fb = expect("feedback list", call("GET", "/feedback"), 200)
check("seeded feedback", fb["count"] >= 6)
expect("feedback post", call("POST", "/feedback", json={"name": "Test User", "rating": 5, "comment": "Great experience overall"}), 201)
expect("feedback too short", call("POST", "/feedback", json={"name": "Test", "rating": 5, "comment": "short"}), 422)
expect("contact", call("POST", "/contact", json={"name": "Test", "email": "t@example.com", "phone": "+92 300 1234567",
                                                 "message": "Please bring flowQ to our office"}), 201)

print("== auth")
email = f"new.{uuid.uuid4().hex[:6]}@example.com"
reg = expect("register", call("POST", "/auth/register", json={"full_name": "New Person", "email": email,
                                                              "phone": "+92 311 2223334", "password": "secret123"}), 201)
check("register returns customer", reg["user"]["role"] == "customer")
expect("duplicate register", call("POST", "/auth/register", json={"full_name": "New Person", "email": email,
                                                                  "phone": "+92 311 2223334", "password": "secret123"}), 409)
expect("bad password login", call("POST", "/auth/login", json={"email": "admin@flowq.demo", "password": "nope12345"}), 401)
expect("no token", call("GET", "/auth/me"), 401)
expect("garbage token", call("GET", "/auth/me", "abc.def.ghi"), 401)
NEW = reg["access_token"]
CUS, STF, MGR, ADM = (login(e) for e in ("customer@flowq.demo", "staff@flowq.demo", "manager@flowq.demo", "admin@flowq.demo"))
me = expect("me (staff)", call("GET", "/auth/me", STF), 200)
check("staff permissions", "call_tokens" in me["permissions"] and "manage_users" not in me["permissions"])

print("== permissions are enforced")
expect("customer -> staff console", call("GET", "/staff/console", CUS), 403)
expect("customer -> manager", call("GET", "/manager/overview", CUS), 403)
expect("customer -> admin users", call("GET", "/admin/users", CUS), 403)
expect("staff -> manager staff", call("GET", "/manager/staff", STF), 403)
expect("staff -> admin", call("GET", "/admin/departments", STF), 403)
expect("staff -> book", call("POST", "/appointments", STF, json={"service_id": 1, "date": str(date.today()), "slot_start": "16:00"}), 403)
expect("manager -> admin permissions", call("GET", "/admin/permissions", MGR), 403)
expect("manager -> call next", call("POST", "/staff/call-next", MGR), 403)
expect("manager other department", call("GET", f"/manager/overview?department_id={REG['id']}", MGR), 403)
expect("admin manager view needs dept", call("GET", "/manager/overview", ADM), 400)
expect("admin manager view with dept", call("GET", f"/manager/overview?department_id={REG['id']}", ADM), 200)

print("== customer: appointments")
tomorrow = (date.today() + timedelta(days=1)).isoformat()
av = expect("availability", call("GET", f"/departments/{SA['id']}/availability?date={tomorrow}", NEW), 200)
s900 = next(s for s in av["slots"] if s["start"] == "09:00")
check("09:00 tomorrow is full (6/6)", s900["full"] and s900["booked"] == 6, s900)
svc = SA["services"][0]["id"]
expect("book full slot", call("POST", "/appointments", NEW, json={"service_id": svc, "date": tomorrow, "slot_start": "09:00"}), 409)
expect("book off-grid slot", call("POST", "/appointments", NEW, json={"service_id": svc, "date": tomorrow, "slot_start": "09:10"}), 400)
expect("book far future", call("POST", "/appointments", NEW, json={"service_id": svc, "date": str(date.today() + timedelta(days=60)), "slot_start": "09:00"}), 400)
ap = expect("book 14:00", call("POST", "/appointments", NEW, json={"service_id": svc, "date": tomorrow, "slot_start": "14:00", "reminder": "SMS"}), 201)
check("appointment confirmed with code", ap["status"] == "Confirmed" and ap["code"].startswith("UN-"))
expect("double-book same time elsewhere", call("POST", "/appointments", NEW, json={"service_id": REG["services"][0]["id"], "date": tomorrow, "slot_start": "14:00"}), 409)
expect("other customer can't see it", call("GET", f"/appointments/{ap['id']}", CUS), 404)
ap = expect("reschedule", call("POST", f"/appointments/{ap['id']}/reschedule", NEW, json={"date": tomorrow, "slot_start": "14:30"}), 200)
check("status Rescheduled", ap["status"] == "Rescheduled" and ap["slot_start"] == "14:30")
expect("reminder", call("PATCH", f"/appointments/{ap['id']}/reminder", NEW, json={"reminder": "Email"}), 200)
expect("check in tomorrow (too early)", call("POST", f"/appointments/{ap['id']}/check-in", NEW), 409)
ap = expect("cancel", call("POST", f"/appointments/{ap['id']}/cancel", NEW), 200)
check("status Cancelled", ap["status"] == "Cancelled")
expect("cancel twice", call("POST", f"/appointments/{ap['id']}/cancel", NEW), 409)

print("== customer: walk-in queue")
mine = expect("my tickets (demo customer)", call("GET", "/queue/my", CUS), 200)
check("demo customer is waiting in Student Affairs", len(mine) == 1 and mine[0]["status"] == "waiting", mine)
my_pos0 = mine[0]["position"]
tk = expect("join queue", call("POST", "/queue/join", NEW, json={"service_id": svc}), 201)
check("token issued, at end of line", tk["status"] == "waiting" and tk["position"] == my_pos0 + 1, tk)
expect("join twice", call("POST", "/queue/join", NEW, json={"service_id": svc}), 409)
hist = expect("history", call("GET", "/me/history", CUS), 200)
check("history has appointments and walk-ins", {"appointment", "walkin"} <= {h["type"] for h in hist})
notes = expect("notifications", call("GET", "/notifications", CUS), 200)
check("notifications exist", notes["items"] and notes["unread"] >= 1)
expect("read all", call("POST", "/notifications/read-all", CUS), 200)

print("== staff console")
con = expect("console", call("GET", "/staff/console", STF), 200)
check("counter 3 Available", con["counter"]["name"] == "Counter 3" and con["counter"]["status"] == "Available", con["counter"])
first = con["waiting"][0]
d = expect("customer details", call("GET", f"/staff/tickets/{first['id']}", STF), 200)
check("details include phone", d["phone"] != "—")
con = expect("call next", call("POST", "/staff/call-next", STF), 200)
check("called first in line", con["current"]["token"] == first["token"] and con["current"]["status"] == "called")
expect("call again blocked", call("POST", "/staff/call-next", STF), 409)
expect("status change blocked while serving", call("PUT", "/staff/status", STF, json={"status": "Break"}), 409)
expect("complete before start", call("POST", "/staff/complete", STF), 409)
expect("recall", call("POST", "/staff/recall", STF), 200)
con = expect("skip", call("POST", "/staff/skip", STF), 200)
check("skipped goes to end", con["current"] is None and con["waiting"][-1]["token"] == first["token"] and con["waiting"][-1]["skipped"])
check("skip counted", con["stats"]["skipped_today"] == 1)
pos = expect("demo customer moved up", call("GET", "/queue/my", CUS), 200)[0]
check("position went down by one", pos["position"] == my_pos0 - 1, (pos["position"], my_pos0))
con = expect("call next (2)", call("POST", "/staff/call-next", STF), 200)
expect("start", call("POST", "/staff/start", STF), 200)
con = expect("complete", call("POST", "/staff/complete", STF), 200)
check("served count up", con["stats"]["served_today"] >= 1 and con["current"] is None)
expect("status Break", call("PUT", "/staff/status", STF, json={"status": "Break"}), 200)
expect("call while on break", call("POST", "/staff/call-next", STF), 409)
expect("status Available", call("PUT", "/staff/status", STF, json={"status": "Available"}), 200)
expect("bad status", call("PUT", "/staff/status", STF, json={"status": "Sleeping"}), 422)

print("== customer leaves queue")
expect("leave queue", call("POST", f"/queue/tickets/{tk['id']}/cancel", NEW), 200)
expect("leave twice", call("POST", f"/queue/tickets/{tk['id']}/cancel", NEW), 409)

print("== manager")
ov = expect("overview", call("GET", "/manager/overview", MGR), 200)
check("overview numbers", ov["department"]["name"] == "Student Affairs" and ov["waiting"] >= 1 and ov["active_counters"] >= 1, ov)
st = expect("add staff", call("POST", "/manager/staff", MGR, json={"full_name": "Test Staffer", "email": f"ts.{uuid.uuid4().hex[:5]}@flowq.demo", "password": "secret123"}), 201)
new_staff = next(s for s in st if s["full_name"] == "Test Staffer")
cs = expect("counters", call("GET", "/manager/counters", MGR), 200)
cs = expect("add counter", call("POST", "/manager/counters", MGR), 201)
newc = cs["counters"][-1]
expect("open counter without staff", call("PUT", f"/manager/counters/{newc['id']}/status", MGR, json={"status": "Active"}), 409)
expect("assign staff", call("PUT", f"/manager/counters/{newc['id']}/staff", MGR, json={"staff_id": new_staff["id"]}), 200)
cs = expect("open counter", call("PUT", f"/manager/counters/{newc['id']}/status", MGR, json={"status": "Active"}), 200)
check("counter now Available", next(x for x in cs["counters"] if x["id"] == newc["id"])["status"] == "Available")
ts = login(next(s for s in st if s["full_name"] == "Test Staffer")["email"], "secret123")
expect("new staff can log in and see console", call("GET", "/staff/console", ts), 200)
expect("remove staff", call("DELETE", f"/manager/staff/{new_staff['id']}", MGR), 200)
expect("removed staff locked out", call("GET", "/staff/console", ts), 401)
expect("remove counter", call("DELETE", f"/manager/counters/{newc['id']}", MGR), 200)
sv = expect("create service", call("POST", "/manager/services", MGR, json={"name": "Hostel letter", "avg_minutes": 7}), 201)
hs = next(s for s in sv if s["name"] == "Hostel letter")
expect("duplicate service", call("POST", "/manager/services", MGR, json={"name": "hostel letter", "avg_minutes": 7}), 409)
expect("toggle service", call("PATCH", f"/manager/services/{hs['id']}", MGR, json={"active": False}), 200)
expect("join unavailable service", call("POST", "/queue/join", NEW, json={"service_id": hs["id"]}), 409)
expect("delete service", call("DELETE", f"/manager/services/{hs['id']}", MGR), 200)
other_svc = REG["services"][0]["id"]
expect("can't edit other dept service", call("PATCH", f"/manager/services/{other_svc}", MGR, json={"active": False}), 404)
sc = expect("schedule", call("GET", "/manager/schedule", MGR), 200)
expect("bad hours", call("PUT", "/manager/schedule", MGR, json={"open_time": "17:00", "close_time": "09:00"}), 400)
expect("bad duration", call("PUT", "/manager/schedule", MGR, json={"slot_minutes": 20}), 422)
sc = expect("set hours/limit", call("PUT", "/manager/schedule", MGR, json={"open_time": "08:30", "close_time": "16:30", "slot_minutes": 30, "daily_limit": 130}), 200)
check("schedule saved", sc["open_time"] == "08:30" and sc["daily_limit"] == 130)
expect("restore schedule", call("PUT", "/manager/schedule", MGR, json={"open_time": "09:00", "close_time": "17:00", "daily_limit": 120}), 200)
rp = expect("reports", call("GET", "/manager/reports", MGR), 200)
check("workload rows", len(rp["workload"]) >= 4)

print("== admin")
dp = expect("departments", call("GET", "/admin/departments", ADM), 200)
dp = expect("add department", call("POST", "/admin/departments", ADM, json={"name": "Hostel Office", "place_id": 1}), 201)
ho = next(x for x in dp["departments"] if x["name"] == "Hostel Office")
expect("duplicate department", call("POST", "/admin/departments", ADM, json={"name": "hostel office", "place_id": 1}), 409)
expect("toggle department", call("PATCH", f"/admin/departments/{ho['id']}", ADM, json={"active": False}), 200)
sv = expect("admin add service", call("POST", "/admin/services", ADM, json={"name": "Room allotment", "department_id": ho["id"], "avg_minutes": 10}), 201)
expect("delete dept with services", call("DELETE", f"/admin/departments/{ho['id']}", ADM), 409)
ra = next(s for s in sv if s["name"] == "Room allotment")
expect("admin remove service", call("DELETE", f"/admin/services/{ra['id']}", ADM), 200)
expect("delete dept", call("DELETE", f"/admin/departments/{ho['id']}", ADM), 200)
us = expect("users", call("GET", "/admin/users", ADM), 200)
admin_id = next(u["id"] for u in us if u["email"] == "admin@flowq.demo")
expect("can't demote self", call("PATCH", f"/admin/users/{admin_id}", ADM, json={"role": "customer"}), 409)
expect("can't remove self", call("DELETE", f"/admin/users/{admin_id}", ADM), 409)
expect("staff needs department", call("POST", "/admin/users", ADM, json={"full_name": "No Dept", "email": "nodept@flowq.demo", "password": "secret123", "role": "staff"}), 400)
us = expect("add manager", call("POST", "/admin/users", ADM, json={"full_name": "Second Admin", "email": "admin2@flowq.demo", "password": "secret123", "role": "admin"}), 201)
a2 = next(u for u in us if u["email"] == "admin2@flowq.demo")
A2 = login("admin2@flowq.demo", "secret123")
expect("second admin can remove first", call("PATCH", f"/admin/users/{admin_id}", A2, json={"active": False}), 200)
expect("only remaining admin can't deactivate self", call("PATCH", f"/admin/users/{a2['id']}", A2, json={"active": False}), 409)
expect("deactivated admin locked out", call("GET", "/admin/users", ADM), 401)
expect("reactivate admin", call("PATCH", f"/admin/users/{admin_id}", A2, json={"active": True}), 200)
ADM = login("admin@flowq.demo")
expect("remove admin2", call("DELETE", f"/admin/users/{a2['id']}", ADM), 200)
cust_id = next(u["id"] for u in us if u["email"] == "customer@flowq.demo")
expect("promote customer to staff w/ dept", call("PATCH", f"/admin/users/{cust_id}", ADM, json={"role": "staff", "department_id": REG["id"]}), 200)
expect("back to customer", call("PATCH", f"/admin/users/{cust_id}", ADM, json={"role": "customer"}), 200)
pm = expect("permissions", call("GET", "/admin/permissions", ADM), 200)
check("admin has everything", len(pm["matrix"]["admin"]) == len(pm["permissions"]))
expect("can't change admin", call("PUT", "/admin/permissions", ADM, json={"role": "admin", "permission": "manage_users", "allowed": False}), 409)
expect("can't grant manage_permissions", call("PUT", "/admin/permissions", ADM, json={"role": "manager", "permission": "manage_permissions", "allowed": True}), 409)
expect("remove join_queue from customers", call("PUT", "/admin/permissions", ADM, json={"role": "customer", "permission": "join_queue", "allowed": False}), 200)
expect("customer now blocked", call("POST", "/queue/join", NEW, json={"service_id": svc}), 403)
expect("restore join_queue", call("PUT", "/admin/permissions", ADM, json={"role": "customer", "permission": "join_queue", "allowed": True}), 200)
expect("grant manager view_reports", call("PUT", "/admin/permissions", ADM, json={"role": "manager", "permission": "view_reports", "allowed": True}), 200)
expect("manager can now view reports", call("GET", "/admin/reports", MGR), 200)
expect("revoke again", call("PUT", "/admin/permissions", ADM, json={"role": "manager", "permission": "view_reports", "allowed": False}), 200)
expect("manager blocked again", call("GET", "/admin/reports", MGR), 403)
act = expect("activity", call("GET", "/admin/activity?category=Permissions", ADM), 200)
check("activity logged", act["feed"] and all(a["category"] == "Permissions" for a in act["feed"]))
for rng in ("today", "week", "month"):
    r = expect(f"report {rng}", call("GET", f"/admin/reports?range={rng}", ADM), 200)
check("month report has data", r["metrics"][0]["value"] > 100 and any(h["value"] for h in r["hours"]), r["metrics"])
csv_r = call("GET", "/admin/reports.csv?range=week", ADM)
check("csv export", csv_r.status_code == 200 and "Visitors served" in csv_r.text)

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
