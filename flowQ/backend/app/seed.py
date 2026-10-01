"""Create the flowQ tables in Supabase and fill them with demo data.

    python -m app.seed --reset        # drops the flowQ tables, recreates them, loads demo data

Dates are relative to *now* (APP_TIMEZONE), so re-run it any time you want a fresh "today":
a live queue in Student Affairs, 30 days of history for the reports and bookings for the coming week.
"""
import argparse
import random
import sys
import uuid
from datetime import date, datetime, time, timedelta

from .config import PROJECT_DIR, settings
from .db import close_pool, tx
from .permissions import DEFAULT_GRANTS
from .security import hash_password
from .timeutil import TZ, from_minutes, local_dt, minutes, now_local, slots_for

DEMO_PASSWORD = "flowq1234"

# place slug, name, description, icon, appointment prefix, hours, departments(name, token prefix, services)
PLACES = [
    ("university", "University offices", "Exams, admissions, fees and student services.", "🎓", "UN", ("09:00", "17:00"), [
        ("Examination", "E", [("Result card", 10), ("Re-checking request", 15), ("Exam schedule query", 5)]),
        ("Student Affairs", "A", [("ID card", 5), ("Enrollment letter", 10), ("New registration", 20)]),
        ("Registrar", "R", [("Transcript", 12), ("Course change", 10), ("Degree verification", 15)]),
        ("Finance", "F", [("Fee query", 8), ("Refund", 15), ("Fee challan", 5)]),
    ]),
    ("banks", "Banks", "Accounts, cash, cards and loans.", "🏦", "BK", ("09:00", "17:00"), [
        ("Account Opening", "A", [("Savings account", 25), ("Current account", 30)]),
        ("Cash Services", "C", [("Cash deposit or withdrawal", 5), ("Cheque book request", 8)]),
        ("Card Services", "D", [("New card", 15), ("Card replacement", 12), ("PIN reset", 6)]),
        ("Loans", "L", [("Loan enquiry", 15), ("Loan application", 30)]),
    ]),
    ("clinics", "Clinics", "Check-ups, lab tests and consultations.", "🩺", "CL", ("08:00", "18:00"), [
        ("General Check-up", "G", [("General check-up", 15), ("Follow-up visit", 10), ("Vaccination", 5)]),
        ("Lab Tests", "L", [("Blood test", 8), ("Urine test", 5), ("X-ray", 12)]),
        ("Pharmacy", "P", [("Pharmacy pickup", 4)]),
        ("Specialist Consultation", "S", [("Specialist consultation", 20), ("Dental check-up", 20)]),
    ]),
]

# department -> (manager, [staff...]); (name, email or None). The first staff member gets Counter 1, etc.
PEOPLE = {
    "Student Affairs": (("Sara Malik", "manager@flowq.demo"),
                        [("Amina Khan", "amina.khan@flowq.demo"), ("Daniel Ross", "daniel.ross@flowq.demo"),
                         ("Bilal Ahmed", "staff@flowq.demo"), ("Omar Farid", "omar.farid@flowq.demo"),
                         ("Lina Haddad", "lina.haddad@flowq.demo")]),
    "Cash Services": (("Imran Qureshi", "bank.manager@flowq.demo"),
                      [("Kashif Mehmood", "bank.staff@flowq.demo"), ("Sana Javed", None)]),
    "General Check-up": (("Dr. Nadia Hussain", "clinic.manager@flowq.demo"),
                         [("Rabia Anwar", "clinic.staff@flowq.demo"), ("Faisal Karim", None)]),
    "Examination": (("Tariq Mahmood", None), [("Noreen Akhtar", None), ("Junaid Iqbal", None)]),
    "Registrar": (("Shazia Parveen", None), [("Adnan Siddiqui", None), ("Mehwish Ali", None)]),
    "Finance": (("Khalid Rehman", None), [("Uzma Naveed", None), ("Waqas Haider", None)]),
    "Account Opening": (("Asma Rauf", None), [("Haris Sohail", None), ("Iqra Aziz", None)]),
    "Card Services": (("Naveed Akram", None), [("Saima Noor", None), ("Yasir Shah", None)]),
    "Loans": (("Farah Deeba", None), [("Zeeshan Ali", None), ("Kiran Bashir", None)]),
    "Lab Tests": (("Dr. Salman Raza", None), [("Hina Altaf", None), ("Owais Khan", None)]),
    "Pharmacy": (("Mariam Qadir", None), [("Bushra Ansari", None), ("Shoaib Malik", None)]),
    "Specialist Consultation": (("Dr. Ahsan Javed", None), [("Sadia Imam", None), ("Hamid Mir", None)]),
}

CUSTOMERS = ["Hamza Ali", "Maryam Noor", "Usman Tariq", "Zainab Raza", "Ali Hassan", "Fatima Sheikh", "Ahmed Raza",
             "Hina Iqbal", "Saad Akram", "Mahnoor Butt", "Danish Kamal", "Areeba Siddiqui", "Fahad Mustafa", "Sidra Batool",
             "Rizwan Ahmed", "Komal Rizvi", "Talha Anjum", "Noor Fatima", "Ammar Khalid", "Laiba Khan", "Shahzaib Ali",
             "Eman Zahid", "Arslan Nasir", "Rimsha Aslam", "Hassan Raza", "Aiman Tahir", "Bilal Saeed", "Anam Shah",
             "Junaid Khan"]

FEEDBACK = [
    (5, "Booked from my phone and only walked in when my token was called. No more standing in line.", "Hamza Ali", "Student Affairs"),
    (5, "The live tracker told me exactly how long I had. I had lunch and got called right on time.", "Maryam Noor", "Registrar"),
    (4, "Loved the alert when my turn was close. Would love SMS reminders too.", "Usman Tariq", "Finance"),
    (5, "Rescheduling my appointment took two taps. Much easier than calling the office.", "Zainab Raza", "Student Affairs"),
    (5, "The walk-in token took ten seconds. I waited at the café next door.", "Ali Hassan", "Cash Services"),
    (4, "Clear wait times and no surprises. The voice call is a nice touch.", "Fatima Sheikh", "General Check-up"),
]


def email_for(name: str) -> str:
    return ".".join(name.lower().replace("dr. ", "").split()) + "@flowq.demo"


class Seeder:
    def __init__(self):
        self.rng = random.Random(2026)
        self.now = now_local()
        self.today = self.now.date()
        self.day_start = local_dt(self.today, time(0, 0))
        self.pw = hash_password(DEMO_PASSWORD)
        self.places, self.depts, self.services, self.users, self.counters = [], [], [], [], []
        self.appts, self.tickets, self.notes, self.activity = [], [], [], []
        self.code_n = 100
        self.booked: set = set()   # (user_id, date, slot_start) so no customer is double-booked

    # ------------------------------------------------------------ structure
    def build(self):
        did = sid = cid = 0
        for i, (slug, name, desc, icon, pre, hours, depts) in enumerate(PLACES, 1):
            self.places.append((i, slug, name, desc, icon, pre, i))
            o, c = time.fromisoformat(hours[0]), time.fromisoformat(hours[1])
            for dname, tp, svcs in depts:
                did += 1
                d = {"id": did, "place_id": i, "name": dname, "prefix": tp, "open": o, "close": c, "appt_pre": pre,
                     "services": [], "staff": [], "counters": []}
                for sname, mins in svcs:
                    sid += 1
                    d["services"].append({"id": sid, "name": sname, "avg": mins})
                    self.services.append((sid, did, sname, mins))
                self.depts.append(d)
                (mname, memail), staff = PEOPLE[dname]
                self.user(mname, memail or email_for(mname), "manager", did)
                for k, (sname, semail) in enumerate(staff):
                    d["staff"].append(self.user(sname, semail or email_for(sname), "staff", did))
                n_counters = 4 if dname == "Student Affairs" else 3
                for k in range(n_counters):
                    cid += 1
                    staff_id = d["staff"][k]["id"] if k < len(d["staff"]) and k < n_counters else None
                    status = "Available" if staff_id else "Closed"
                    d["counters"].append({"id": cid, "name": f"Counter {k + 1}", "staff": staff_id, "status": status})
        self.admin = self.user("Admin User", "admin@flowq.demo", "admin", None)
        self.me = self.user("Ayesha Khan", "customer@flowq.demo", "customer", None, phone="+92 300 1111111")
        self.customers = [self.user(n, email_for(n), "customer", None, phone=f"+92 30{k % 10} {1000000 + k * 7919 % 9000000}")
                          for k, n in enumerate(CUSTOMERS)]
        self.dept = {d["name"]: d for d in self.depts}
        sa = self.dept["Student Affairs"]
        sa["counters"][0]["status"] = "Busy"       # Amina is serving A-021
        sa["counters"][3]["status"] = "Break"      # Omar is on a break; Lina is not assigned

    def user(self, name, email, role, dept_id, phone=None) -> dict:
        u = {"id": uuid.uuid4(), "name": name, "email": email, "role": role, "dept": dept_id, "phone": phone,
             "created": self.now - timedelta(days=60)}
        self.users.append(u)
        return u

    # ------------------------------------------------------------ helpers
    def code(self, d) -> str:
        self.code_n += 1
        return f"{d['appt_pre']}-{self.code_n:04d}"

    def slot_of(self, d, when: datetime, slot_minutes=30) -> tuple[time, time]:
        grid = slots_for(d["open"], d["close"], slot_minutes)
        m = minutes(when.astimezone(TZ).time())
        for s, e in grid:
            if minutes(s) <= m < minutes(e):
                return s, e
        return grid[0] if m < minutes(grid[0][0]) else grid[-1]

    def appointment(self, d, svc, user, day: date, slot, status, created=None, checked_in=None, reminder=None) -> dict:
        a = {"id": uuid.uuid4(), "code": self.code(d), "user": user["id"], "dept": d["id"], "svc": svc["id"], "date": day,
             "s": slot[0], "e": slot[1], "status": status, "reminder": reminder, "checked_in": checked_in,
             "created": created or local_dt(day, slot[0]) - timedelta(days=self.rng.randint(1, 6))}
        self.appts.append(a)
        self.booked.add((user["id"], day, slot[0]))
        return a

    def ticket(self, d, svc, user, day, number, created, status, kind="walkin", appt=None, counter=None, staff=None,
               first_called=None, started=None, closed=None) -> dict:
        t = {"id": uuid.uuid4(), "dept": d["id"], "svc": svc["id"], "user": user["id"] if user else None,
             "appt": appt["id"] if appt else None, "date": day, "number": number, "token": f"{d['prefix']}-{number:03d}",
             "kind": kind, "status": status, "counter": counter["id"] if counter else None,
             "counter_name": counter["name"] if counter else None, "staff": staff, "sort_at": created,
             "created": created, "first_called": first_called, "called": first_called if status in ("called", "serving", "completed", "no_show") else None,
             "started": started, "closed": closed, "near": False}
        self.tickets.append(t)
        return t

    def act(self, cat, msg, when, actor=None, dept=None, ticket=None, event=None):
        self.activity.append((cat, msg, event, actor, dept, ticket, when))

    def served_ticket(self, d, day, number, created, kind="walkin", user=None, svc=None, staff_counter=None, force=None):
        rng = self.rng
        svc = svc or rng.choice(d["services"])
        rush = 8 if 11 <= created.astimezone(TZ).hour <= 13 else 0
        called = created + timedelta(minutes=rng.randint(2, 22) + rush, seconds=rng.randint(0, 59))
        counter = staff_counter or rng.choice([c for c in d["counters"] if c["staff"]] or d["counters"])
        r = rng.random()
        status = force or ("completed" if r < .9 else "no_show" if r < .96 else "cancelled")
        appt = None
        if kind == "appointment":
            ast = {"completed": "Completed", "no_show": "Missed", "cancelled": "Cancelled"}[status]
            appt = self.appointment(d, svc, user, day, self.slot_of(d, created), ast, checked_in=created)
        if status == "completed":
            started = called + timedelta(minutes=1)
            closed = started + timedelta(minutes=max(2, svc["avg"] + rng.randint(-3, 5)))
            return self.ticket(d, svc, user, day, number, created, status, kind, appt, counter, counter["staff"], called, started, closed)
        if status == "no_show":
            return self.ticket(d, svc, user, day, number, created, status, kind, appt, counter, counter["staff"], called, None,
                               called + timedelta(minutes=6))
        return self.ticket(d, svc, user, day, number, created, status, kind, appt, None, None, None, None,
                           created + timedelta(minutes=rng.randint(3, 20)))

    # ------------------------------------------------------------ history: last 30 days
    def history(self):
        rng = self.rng
        for d in self.depts:
            base = 34 if d["name"] == "Student Affairs" else rng.randint(10, 22)
            o, c = minutes(d["open"]), minutes(d["close"])
            for back in range(30, 0, -1):
                day = self.today - timedelta(days=back)
                n = max(3, int(base * (0.45 if day.weekday() == 6 else 1) * rng.uniform(.75, 1.2)))
                stamps = sorted(rng.randint(o, c - 15) for _ in range(n))
                for num, m in enumerate(stamps, 1):
                    created = local_dt(day, from_minutes(m)) + timedelta(seconds=rng.randint(0, 59))
                    kind = "appointment" if rng.random() < .3 else "walkin"
                    user = rng.choice(self.customers) if kind == "appointment" or rng.random() < .85 else None
                    self.served_ticket(d, day, num, created, kind, user)
                for _ in range(rng.randint(0, 2)):          # booked but never showed up
                    s = rng.choice(slots_for(d["open"], d["close"], 30))
                    self.appointment(d, rng.choice(d["services"]), rng.choice(self.customers), day, s, "Missed")
                if rng.random() < .5:
                    s = rng.choice(slots_for(d["open"], d["close"], 30))
                    self.appointment(d, rng.choice(d["services"]), rng.choice(self.customers), day, s, "Cancelled")

    # ------------------------------------------------------------ today: a live queue
    def live_today(self):
        rng, now, day = self.rng, self.now, self.today
        cust = {u["name"]: u for u in self.customers}

        def clamp(dt, i):
            return max(dt, self.day_start + timedelta(seconds=i + 1))

        for d in self.depts:
            is_sa = d["name"] == "Student Affairs"
            done = 20 if is_sa else rng.randint(4, 11)
            start_m = 200 if is_sa else rng.randint(90, 180)
            served_by = [c for c in d["counters"] if c["staff"] and c["status"] == "Available"] or d["counters"]
            for i in range(done):
                created = clamp(now - timedelta(minutes=start_m - i * (start_m - 26) / max(1, done)), i)
                user = rng.choice(self.customers) if rng.random() < .8 else None
                t = self.served_ticket(d, day, i + 1, created, "walkin", user, staff_counter=served_by[i % len(served_by)],
                                       force="completed")
                # keep "today" in the past: shorten if the computed times run past now
                for k in ("first_called", "started", "closed"):
                    if t[k] and t[k] > now - timedelta(minutes=1):
                        t[k] = max(created + timedelta(seconds=30), now - timedelta(minutes=2))
                t["called"] = t["first_called"]
                self.act("Queue", f"{t['token']} completed at {t['counter_name']} ({d['name']})", t["closed"],
                         actor=t["staff"], dept=d["id"], ticket=t["id"], event="completed")
            num = done
            if is_sa:
                c1 = d["counters"][0]
                num += 1
                svc = d["services"][2]
                created = clamp(now - timedelta(minutes=24), num)
                t = self.ticket(d, svc, cust["Fatima Sheikh"], day, num, created, "serving", counter=c1, staff=c1["staff"],
                                first_called=clamp(now - timedelta(minutes=6), num), started=clamp(now - timedelta(minutes=5), num))
                self.act("Queue", f"{t['token']} called at Counter 1 (Student Affairs)", t["first_called"], c1["staff"], d["id"], t["id"], "called")
                waiting = [("Hamza Ali", 0, "appointment"), ("Maryam Noor", 1, "appointment"), ("Usman Tariq", 1, "walkin"),
                           ("Zainab Raza", 0, "walkin"), ("Ali Hassan", 2, "appointment"), (None, 0, "walkin")]
                for k, (who, si, kind) in enumerate(waiting):
                    num += 1
                    user = cust[who] if who else self.me
                    created = clamp(now - timedelta(minutes=19 - k * 3), num)
                    svc = d["services"][si]
                    appt = None
                    if kind == "appointment":
                        appt = self.appointment(d, svc, user, day, self.slot_of(d, now), "Waiting", checked_in=created)
                    t = self.ticket(d, svc, user, day, num, created, "waiting", kind, appt)
                    self.act("Queue", f"{'Appointment check-in, token' if appt else 'Walk-in token'} {t['token']} issued "
                                      f"(Student Affairs · {svc['name']})", created, user["id"], d["id"], t["id"], "issued")
                me_t = self.tickets[-1]
                self.notes += [(self.me["id"], f"Your token is {me_t['token']}", "Student Affairs · ID card. Track your place live in flowQ.",
                                False, me_t["created"])]
            else:
                pool = [u for u in self.customers if u["name"] not in ("Hamza Ali", "Maryam Noor", "Usman Tariq", "Zainab Raza", "Ali Hassan")]
                for k, user in enumerate(rng.sample(pool, rng.randint(1, 4))):
                    num += 1
                    created = clamp(now - timedelta(minutes=rng.randint(1, 20)), num + k)
                    self.ticket(d, rng.choice(d["services"]), user, day, num, created, "waiting")
            self.tickets.sort(key=lambda t: (t["dept"], t["date"], t["number"]))
        # re-number today's waiting tokens per department in arrival order (random clamps can reorder)
        for d in self.depts:
            todays = sorted((t for t in self.tickets if t["dept"] == d["id"] and t["date"] == day), key=lambda t: t["created"])
            for i, t in enumerate(todays, 1):
                t["number"], t["token"] = i, f"{d['prefix']}-{i:03d}"
        sa = self.dept["Student Affairs"]
        self.act("Queue", "Counter 3 opened (Student Affairs)", self.day_start + timedelta(hours=8, minutes=55)
                 if self.now.hour >= 9 else self.now - timedelta(hours=3), sa["counters"][2]["staff"], sa["id"], None, "status")

    # ------------------------------------------------------------ upcoming bookings + the demo customer's own history
    def upcoming(self):
        rng, day = self.rng, self.today
        tomorrow = day + timedelta(days=1)
        others = list(self.customers)
        # tomorrow mirrors the UI: 9:00 full, 9:30 4/6, 10:00 5/6, 10:30 2/6
        for name in ("Student Affairs", "Cash Services", "General Check-up"):
            d = self.dept[name]
            for hhmm, n in (("09:00", 6), ("09:30", 4), ("10:00", 5), ("10:30", 2)):
                s = time.fromisoformat(hhmm)
                slot = (s, from_minutes(minutes(s) + 30))
                free = [u for u in others if (u["id"], tomorrow, s) not in self.booked]
                for u in rng.sample(free, n):
                    self.appointment(d, rng.choice(d["services"]), u, tomorrow, slot, "Confirmed",
                                     reminder=rng.choice(["Email", "SMS", "In-app alert"]))
        for d in self.depts:
            grid = slots_for(d["open"], d["close"], 30)
            for ahead in range(0, 8):
                day_x = day + timedelta(days=ahead)
                for _ in range(rng.randint(1, 4)):
                    s = rng.choice(grid)
                    if ahead == 0 and minutes(s[0]) <= minutes(self.now.time()):
                        continue
                    free = [u for u in others if (u["id"], day_x, s[0]) not in self.booked]
                    self.appointment(d, rng.choice(d["services"]), rng.choice(free), day_x, s,
                                     rng.choice(["Confirmed", "Confirmed", "Rescheduled"]))
        me = self.me
        R, G = self.dept["Registrar"], self.dept["General Check-up"]
        a1 = self.appointment(R, R["services"][0], me, tomorrow, (time(11, 0), time(11, 30)), "Rescheduled", reminder="Email")
        a2 = self.appointment(G, G["services"][1], me, day + timedelta(days=3), (time(10, 0), time(10, 30)), "Confirmed", reminder="SMS")
        SA, F, CS, CA = self.dept["Student Affairs"], self.dept["Finance"], self.dept["Card Services"], self.dept["Cash Services"]
        d5 = day - timedelta(days=5)
        t = self.served_ticket(SA, d5, 99, local_dt(d5, time(9, 25)), "appointment", me, SA["services"][0], force="completed")
        a0 = next(a for a in self.appts if a["id"] == t["appt"])
        self.appointment(F, F["services"][0], me, day - timedelta(days=9), (time(14, 0), time(14, 30)), "Missed")
        self.appointment(CS, CS["services"][1], me, day - timedelta(days=12), (time(11, 30), time(12, 0)), "Cancelled")
        d2 = day - timedelta(days=2)
        self.served_ticket(CA, d2, 98, local_dt(d2, time(12, 10)), "walkin", me, CA["services"][0], force="completed")
        self.notes += [
            (me["id"], "Welcome to flowQ", "Book an appointment or grab a walk-in token whenever you need one.", True, self.now - timedelta(days=30)),
            (me["id"], f"Appointment {a0['code']} confirmed", f"Student Affairs · ID card on {d5:%a %d %b} at 9:00 AM – 9:30 AM.", True, self.now - timedelta(days=7)),
            (me["id"], f"Appointment {a2['code']} confirmed", f"General Check-up · Follow-up visit on {a2['date']:%a %d %b} at 10:00 AM – 10:30 AM.", True, self.now - timedelta(days=1)),
            (me["id"], f"Appointment {a1['code']} rescheduled", f"New time: {tomorrow:%a %d %b} at 11:00 AM – 11:30 AM.", False, self.now - timedelta(hours=5)),
        ]

    # ------------------------------------------------------------ write
    def write(self, reset: bool):
        schema = (PROJECT_DIR / "supabase" / "schema.sql").read_text(encoding="utf-8")
        with tx() as cur:
            exists = cur.execute("select to_regclass('public.users') is not null as e").fetchone()["e"]
            if exists and not reset:
                sys.exit("flowQ tables already exist. Re-run with --reset to DROP them and load fresh demo data.")
            cur.execute(schema)
            cur.executemany("insert into places (id, slug, name, description, icon, appointment_prefix, sort_order) "
                            "values (%s,%s,%s,%s,%s,%s,%s)", self.places)
            cur.executemany("insert into departments (id, place_id, name, token_prefix, open_time, close_time) values (%s,%s,%s,%s,%s,%s)",
                            [(d["id"], d["place_id"], d["name"], d["prefix"], d["open"], d["close"]) for d in self.depts])
            cur.executemany("insert into services (id, department_id, name, avg_minutes) values (%s,%s,%s,%s)", self.services)
            with cur.copy("copy users (id, full_name, email, phone, password_hash, role, department_id, created_at) from stdin") as cp:
                for u in self.users:
                    cp.write_row((u["id"], u["name"], u["email"], u["phone"], self.pw, u["role"], u["dept"], u["created"]))
            cur.executemany("insert into role_permissions (role, permission) values (%s,%s)", DEFAULT_GRANTS)
            cur.executemany("insert into counters (id, department_id, name, status, staff_id) values (%s,%s,%s,%s,%s)",
                            [(c["id"], d["id"], c["name"], c["status"], c["staff"]) for d in self.depts for c in d["counters"]])
            with cur.copy("copy appointments (id, code, user_id, department_id, service_id, appt_date, slot_start, slot_end, status, "
                          "reminder, checked_in_at, created_at, updated_at) from stdin") as cp:
                for a in self.appts:
                    cp.write_row((a["id"], a["code"], a["user"], a["dept"], a["svc"], a["date"], a["s"], a["e"], a["status"],
                                  a["reminder"], a["checked_in"], a["created"], a["created"]))
            with cur.copy("copy tickets (id, department_id, service_id, user_id, appointment_id, queue_date, number, token, kind, status, "
                          "counter_id, counter_name, staff_id, sort_at, created_at, first_called_at, called_at, started_at, closed_at) from stdin") as cp:
                for t in self.tickets:
                    cp.write_row((t["id"], t["dept"], t["svc"], t["user"], t["appt"], t["date"], t["number"], t["token"], t["kind"],
                                  t["status"], t["counter"], t["counter_name"], t["staff"], t["sort_at"], t["created"],
                                  t["first_called"], t["called"], t["started"], t["closed"]))
            with cur.copy("copy notifications (user_id, title, body, read, created_at) from stdin") as cp:
                for n in self.notes:
                    cp.write_row(n)
            with cur.copy("copy activity_log (category, message, event, actor_id, department_id, ticket_id, created_at) from stdin") as cp:
                for a in sorted(self.activity, key=lambda x: x[-1]):
                    cp.write_row(a)
            cur.executemany("insert into feedback (name, rating, comment, context, created_at) values (%s,%s,%s,%s,%s)",
                            [(n, r, c, ctx, self.now - timedelta(days=k + 1)) for k, (r, c, n, ctx) in enumerate(FEEDBACK)])
            cur.execute("insert into token_counters (department_id, queue_date, last_number) "
                        "select department_id, queue_date, max(number) from tickets group by 1, 2")
            for table in ("places", "departments", "services", "counters"):
                cur.execute(f"select setval(pg_get_serial_sequence('{table}', 'id'), (select max(id) from {table}))")
            cur.execute("select setval('appointment_code_seq', %s)", (self.code_n,))

    def run(self, reset: bool):
        self.build()
        self.history()
        self.live_today()
        self.upcoming()
        self.write(reset)
        print(f"flowQ demo data loaded for {self.today:%A %d %B %Y} ({settings.app_timezone}).")
        print(f"  {len(self.users)} users, {len(self.depts)} departments, {len(self.appts)} appointments, {len(self.tickets)} tokens")
        print(f"  Every demo account uses the password: {DEMO_PASSWORD}")
        for e, r in [("admin@flowq.demo", "Administrator"), ("manager@flowq.demo", "Department Manager (Student Affairs)"),
                     ("staff@flowq.demo", "Service Staff (Student Affairs, Counter 3)"), ("customer@flowq.demo", "Customer")]:
            print(f"  {e:<24} {r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="drop existing flowQ tables first")
    args = ap.parse_args()
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set. Copy backend/.env.example to backend/.env and fill it in.")
    try:
        Seeder().run(args.reset)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
