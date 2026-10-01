# flowQ — Digital Queue & Appointment Management System

FastAPI backend + Supabase (PostgreSQL) database + your flowQ web pages, all wired together.

```
flowQ/
├── backend/
│   ├── app/              FastAPI app (routers: auth, public, customer, staff, manager, admin)
│   ├── tests/smoke_test.py   end-to-end test of every role and permission
│   ├── .env              your settings (DATABASE_URL goes here)
│   └── requirements.txt
├── frontend/             the HTML pages (served by FastAPI at http://localhost:8000)
│   ├── index.html        landing page, login / sign up, customer portal
│   ├── place.html        choose place + department
│   ├── workflow.html     book an appointment or get a walk-in token (any place)
│   ├── staff.html        Service Staff console
│   ├── manager.html      Department Manager dashboard
│   ├── admin.html        Administrator console
│   └── js/api.js         shared API client
├── supabase/schema.sql   database tables (also run automatically by the seeder)
└── run.bat               start the server
```

## 1. Connect Supabase (one time)

1. Create a project at [supabase.com](https://supabase.com) (remember the database password).
2. In the dashboard click **Connect** and copy the **Session pooler** connection string
   (it looks like `postgresql://postgres.abcd1234:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`).
3. Open `backend/.env` and paste it into `DATABASE_URL`, with your real password in place of `[YOUR-PASSWORD]`.
   (`JWT_SECRET` is already filled in with a random value.)

## 2. Create the tables and demo data

```bash
cd backend
..\.venv\Scripts\python -m app.seed --reset
```

This creates every table in Supabase (you'll see them under **Table Editor**) and loads demo data:
12 departments across University offices, Banks and Clinics, 70 users, 30 days of history for the reports,
a live queue in Student Affairs today, and bookings for the next week.

Dates are relative to the moment you run it, so **re-run it whenever you want a fresh "today"** (e.g. right before a demo).
`--reset` drops and recreates the flowQ tables only.

Prefer the SQL editor? Paste `supabase/schema.sql` into Supabase → SQL Editor → Run to create the tables
(the demo data still comes from the command above).

## 3. Run it

Double-click `run.bat` (or run `..\.venv\Scripts\python -m uvicorn app.main:app --port 8000` inside `backend`), then open:

- **http://localhost:8000** — the website
- **http://localhost:8000/docs** — interactive API docs (log in with `POST /api/auth/login`, click **Authorize**, paste the token)

## Demo accounts (password for all: `flowq1234`)

| Email | Role | Opens |
|---|---|---|
| `customer@flowq.demo` | Customer / Visitor (Ayesha Khan, has token A-027 waiting) | index.html portal |
| `staff@flowq.demo` | Service Staff (Bilal Ahmed, Student Affairs, Counter 3) | staff.html |
| `manager@flowq.demo` | Department Manager (Sara Malik, Student Affairs) | manager.html |
| `admin@flowq.demo` | Administrator | admin.html |
| `bank.manager@flowq.demo`, `bank.staff@flowq.demo` | Cash Services manager / staff | |
| `clinic.manager@flowq.demo`, `clinic.staff@flowq.demo` | General Check-up manager / staff | |

Logging in on the home page sends each role to its own dashboard. Dashboards send you back to log in if you aren't.

## Roles and permissions

Every API endpoint checks a permission. Defaults follow the roles table:

| Role | Can |
|---|---|
| Customer / Visitor | create an account, pick department & service, view slots, book, join the queue, get a token, see position & ETA, cancel / reschedule, notifications, history |
| Service Staff | view waiting customers, call next, start, complete, skip, recall, view customer details, update service status |
| Department Manager | manage staff, counters, services, working hours, appointment duration, daily limits; monitor queue length, staff workload, waiting times — **own department only** |
| Administrator | manage departments, users, services, permissions; organisation-wide activity; reports & CSV export — **always has every permission** |

The administrator can tick/untick permissions per role in **Manage permissions**; it takes effect on the very next request.
Safety rules: nobody but an administrator can create or edit administrator accounts or be given "Manage permissions";
you can't deactivate or demote yourself; the last active administrator can't be removed.

## Rules the backend enforces

- Slot capacity (default 6 per slot) and a daily limit per department; nobody can book two appointments at the same time.
- Bookings up to 30 days ahead; past slots can't be booked.
- Check-in opens 1 hour before the slot and closes 30 minutes after it ends (then the appointment is **Missed**).
- One open walk-in token per customer per department. Token numbers restart each day per department (A-001, A-002, …).
- Estimated wait = total service minutes of the people ahead ÷ open counters; it updates instantly when a counter opens or closes.
- Skip moves a called customer to the end of the line; after 3 skips (`MAX_SKIPS`) they're a no-show.
- Yesterday's unfinished tokens are closed automatically.
- Supabase's public REST API is locked out of the tables (Row Level Security on, no policies); only the backend can read/write.

## Settings (`backend/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `APP_TIMEZONE` | `Asia/Karachi` | what "today" and opening hours mean |
| `ENFORCE_WORKING_HOURS` | `false` | `true` = walk-in tokens only during opening hours (`false` lets you demo at any time) |
| `MAX_SKIPS` | `3` | skips before a no-show |
| `JWT_EXPIRE_HOURS` | `12` | how long a login lasts |

## Not included

- Email/SMS sending: notifications are stored and shown in the app; "Forgot password" replies generically but sends nothing.
  Plug a provider (e.g. Resend, Twilio) into `notify()` in `backend/app/queue.py` when you're ready.
- The "Wait nearby" café list on the walk-in page is still sample data (it needs a maps API).

## Testing

```bash
cd backend
..\.venv\Scripts\python -m app.seed --reset
..\.venv\Scripts\python tests\smoke_test.py http://localhost:8000
```
Runs ~140 checks across all four roles. It changes data, so reseed afterwards.
