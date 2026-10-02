# flowQ — Digital Queue & Appointment Management System

flowQ is a full-stack digital queue and appointment management system built around a FastAPI backend, PostgreSQL on Supabase, and a lightweight HTML/CSS/JavaScript frontend.

It is designed for organizations where visitors normally have to wait in physical lines — universities, banks, clinics, and similar service environments. Customers can book appointments or join a live walk-in queue, while staff operate service counters and managers/administrators monitor operations through role-specific dashboards.

## What flowQ provides

- Appointment booking with configurable time slots
- Digital walk-in tokens
- Live queue position and estimated waiting time
- Service-counter management
- Staff queue operations: call, recall, start, complete, and skip
- Customer notifications stored in the application
- Customer appointment and visit history
- Department-level management dashboards
- Organization-wide administration
- Runtime-configurable role permissions
- Queue, waiting-time, workload, and appointment analytics
- CSV report export
- Demo data generation for presentations and testing
- JWT authentication and bcrypt password hashing
- PostgreSQL connection pooling through `psycopg`
- Supabase Row Level Security configured so the public REST layer cannot directly access the application tables

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                         flowQ Frontend                      │
│                                                             │
│ index.html  place.html  workflow.html  staff.html           │
│ manager.html  admin.html  js/api.js                         │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / JSON
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                       │
│                                                             │
│ Auth │ Public │ Customer │ Staff │ Manager │ Administrator │
│                                                             │
│ JWT authentication • permission checks • queue rules        │
│ appointments • notifications • reports • activity logging  │
└──────────────────────────────┬──────────────────────────────┘
                               │ psycopg / connection pool
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  Supabase PostgreSQL                        │
│                                                             │
│ Users • Places • Departments • Services • Counters          │
│ Appointments • Tickets • Notifications • Permissions        │
│ Activity logs • Feedback • Contact messages                 │
└─────────────────────────────────────────────────────────────┘
```

The frontend is served by FastAPI from the same origin as the API. This keeps the local development setup simple: the browser talks to `/api/...`, while FastAPI serves the web pages from `/`.

---

## Project structure

```text
flowQ/
├── backend/
│   ├── app/
│   │   ├── routers/
│   │   │   ├── auth.py          # Authentication and account endpoints
│   │   │   ├── public.py        # Public catalog, feedback, contact, health
│   │   │   ├── customer.py      # Appointments, queues, history, notifications
│   │   │   ├── staff.py         # Counter and queue operations
│   │   │   ├── manager.py       # Department management and statistics
│   │   │   └── admin.py         # Organization-wide administration
│   │   ├── config.py            # Environment-backed configuration
│   │   ├── db.py                # PostgreSQL connection pool and transactions
│   │   ├── deps.py              # Authentication and permission dependencies
│   │   ├── main.py              # FastAPI application entry point
│   │   ├── ops.py               # Shared management/statistics operations
│   │   ├── permissions.py       # Roles and permission catalogue
│   │   ├── queue.py             # Core queue/appointment logic
│   │   ├── schemas.py            # Shared input validation helpers
│   │   ├── security.py           # bcrypt + JWT functions
│   │   ├── seed.py               # Database schema/demo-data seeder
│   │   └── timeutil.py           # Timezone and slot helpers
│   ├── tests/
│   │   └── smoke_test.py         # End-to-end API smoke test
│   └── requirements.txt
│
├── frontend/
│   ├── index.html                # Landing page, authentication, customer portal
│   ├── place.html                # Place and department selection
│   ├── workflow.html             # Appointment + walk-in workflow
│   ├── staff.html                # Service Staff console
│   ├── manager.html              # Department Manager dashboard
│   ├── admin.html                # Administrator console
│   └── js/
│       └── api.js                # Shared frontend API client
│
├── supabase/
│   └── schema.sql                # PostgreSQL schema
│
└── run.bat                       # Windows development launcher
```

---

## Tech stack

### Backend

- Python
- FastAPI
- Uvicorn
- Pydantic
- `psycopg`
- `psycopg-pool`
- bcrypt
- PyJWT
- python-dotenv
- tzdata

### Database

- PostgreSQL
- Supabase
- Supabase Session Pooler
- PostgreSQL transactions and constraints
- Row Level Security

### Frontend

- HTML5
- CSS
- Vanilla JavaScript
- Tailwind CSS CDN
- GSAP
- Lenis
- Google Fonts

No frontend framework or build step is required.

---

## Requirements

For local development you need:

- Python 3.10+ recommended
- A Supabase project with PostgreSQL enabled
- Windows if you want to use the included `run.bat` launcher
- Internet access for the frontend's CDN-hosted UI dependencies

The backend dependencies are installed from:

```text
backend/requirements.txt
```

---

# Getting started

## 1. Clone or copy the project

Place the project somewhere on your machine:

```text
flowQ/
```

Open a terminal in the project directory.

---

## 2. Create a Python virtual environment

The included `run.bat` can create the environment automatically on Windows.

To do it manually:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\activate
```

Then install the backend dependencies:

```bash
pip install -r backend/requirements.txt
```

---

## 3. Create a Supabase project

Create a PostgreSQL project in Supabase.

From the Supabase dashboard:

1. Create a project.
2. Keep the database password available.
3. Open the database connection information.
4. Use the **Session pooler** connection string for `DATABASE_URL`.

A connection string has a structure similar to:

```text
postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres
```

Do not commit your real database password to Git.

---

## 4. Configure environment variables

Create:

```text
backend/.env
```

At minimum:

```env
DATABASE_URL=postgresql://...
JWT_SECRET=replace-with-a-long-random-secret
```

Optional settings:

```env
DB_POOL_SIZE=5
JWT_EXPIRE_HOURS=12
APP_TIMEZONE=Asia/Karachi
ENFORCE_WORKING_HOURS=false
MAX_SKIPS=3
CORS_ORIGINS=*
FRONTEND_DIR=../frontend
```

### Configuration reference

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | — | PostgreSQL/Supabase connection string |
| `DB_POOL_SIZE` | `5` | Maximum database connections in the application pool |
| `JWT_SECRET` | generated at runtime if missing | Secret used to sign JWTs |
| `JWT_EXPIRE_HOURS` | `12` | Login/session lifetime |
| `APP_TIMEZONE` | `Asia/Karachi` | Timezone used for "today", slots, and opening hours |
| `ENFORCE_WORKING_HOURS` | `false` | Restricts walk-in tokens to department opening hours when enabled |
| `MAX_SKIPS` | `3` | Number of skips before a token becomes a no-show |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `FRONTEND_DIR` | `../frontend` | Frontend directory served by FastAPI |

### JWT secret warning

If `JWT_SECRET` is not supplied, flowQ generates a random secret when the application starts. That is convenient for development, but it means existing users will be logged out after a restart.

For any persistent deployment, set a stable secret through the environment.

---

# Database setup

## Option A — Use the built-in seeder

From the `backend` directory:

```powershell
..\.venv\Scripts\python -m app.seed --reset
```

This creates the flowQ database structure and loads the demo dataset.

The generated demo environment includes:

- 12 departments
- University offices, banks, and clinics
- 70 users
- 30 days of historical queue data
- A live queue for the current day
- Appointments for the coming week
- Staff and counters
- Feedback data
- Activity data
- Permission grants

The seed dates are calculated relative to the current time in `APP_TIMEZONE`.

That means you should re-run the seeder before a demonstration if you want the demo's "today" data to be fresh.

> **Warning:** `--reset` drops and recreates the flowQ tables. Do not use it against a database containing production data.

---

## Option B — Create the schema manually

The database schema is also provided at:

```text
supabase/schema.sql
```

You can paste it into:

```text
Supabase → SQL Editor → Run
```

This creates the database structure.

Demo records are still generated by:

```bash
python -m app.seed --reset
```

---

# Run the application

## Windows

The easiest option is:

```text
run.bat
```

The script:

1. Changes into `backend/`
2. Creates `.venv` if necessary
3. Installs `requirements.txt`
4. Starts Uvicorn on `127.0.0.1:8000`

Or run the server manually:

```powershell
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://localhost:8000
```

FastAPI's interactive API documentation is available at:

```text
http://localhost:8000/docs
```

The OpenAPI schema can be inspected through FastAPI's normal documentation interface.

---

# Demo accounts

The seed script creates demo users with the same password:

```text
flowq1234
```

| Email | Role | Main dashboard |
|---|---|---|
| `customer@flowq.demo` | Customer / Visitor | `index.html` |
| `staff@flowq.demo` | Service Staff | `staff.html` |
| `manager@flowq.demo` | Department Manager | `manager.html` |
| `admin@flowq.demo` | Administrator | `admin.html` |
| `bank.manager@flowq.demo` | Department Manager — Cash Services | `manager.html` |
| `bank.staff@flowq.demo` | Service Staff — Cash Services | `staff.html` |
| `clinic.manager@flowq.demo` | Department Manager — General Check-up | `manager.html` |
| `clinic.staff@flowq.demo` | Service Staff — General Check-up | `staff.html` |

The seeded customer account has an active Student Affairs queue entry so the live queue experience can be demonstrated immediately.

### Security note

These credentials are for the demo dataset only. Change or remove them before exposing a deployment to real users.

---

# User roles

flowQ has four application roles.

## Customer / Visitor

Customers can:

- Create an account
- Select a place, department, and service
- View appointment availability
- Book an appointment
- Reschedule an appointment
- Cancel an appointment
- Check in for an appointment
- Join a walk-in queue
- Receive a queue token
- Track queue position
- View estimated waiting time
- View notifications
- View appointment/visit history

---

## Service Staff

Service staff operate the service counter and can:

- View the current queue
- See waiting customers
- Call the next token
- Recall a token
- Start service
- Complete service
- Skip a token
- View customer appointment/contact details
- Change service-counter status

Staff actions are restricted to their department unless the user is an administrator.

---

## Department Manager

Managers can manage their own department:

- Staff members
- Service counters
- Services
- Service availability
- Service duration
- Working hours
- Appointment duration
- Daily appointment limits

They can also monitor:

- Current queue length
- Active counters
- Estimated wait
- Staff workload
- Average waiting time
- Longest waiting time
- Peak queue length
- Waiting time by hour

Department scope is enforced by the backend, not just by the frontend.

---

## Administrator

Administrators have organization-wide access to:

- Departments
- Users
- Services
- Permissions
- Organization activity
- Reports
- CSV exports

Administrators automatically satisfy all application permissions.

The admin console also allows permission grants to be changed for the other roles.

---

# Permission system

Permissions are defined centrally in:

```text
backend/app/permissions.py
```

The current permission catalogue includes:

### Customer permissions

```text
book_appointments
join_queue
view_queue_position
view_history
receive_notifications
```

### Staff permissions

```text
view_waiting_customers
call_tokens
serve_customers
view_customer_details
update_service_status
```

### Manager permissions

```text
manage_staff
manage_counters
manage_department_services
manage_schedule
view_department_stats
```

### Administrator permissions

```text
manage_departments
manage_users
manage_services
manage_permissions
view_org_activity
view_reports
```

Every protected endpoint uses a backend permission dependency.

Changing the permission matrix in the administrator console takes effect on the next request.

### Privilege-escalation protections

The backend prevents:

- Non-administrators from managing permissions
- Granting `manage_permissions` to a non-admin role
- Non-administrators from creating/editing administrator accounts
- Users from deactivating or demoting themselves
- The last active administrator from being removed

---

# Queue and appointment behavior

The queue engine is implemented primarily in:

```text
backend/app/queue.py
```

## Walk-in tokens

Each department has its own token prefix.

Example:

```text
A-001
A-002
A-003
```

Token numbers restart each day for each department.

A customer can have only one open walk-in token per department.

---

## Estimated waiting time

flowQ estimates the waiting time using:

```text
estimated wait
=
total service minutes of customers ahead
÷
number of active counters
```

If there are no active counters, the customer keeps their place in the queue but no numerical ETA is returned.

The ETA is recalculated as counters open or close and as the queue changes.

---

## Appointment slots

The backend enforces:

- Slot capacity
- Department daily limits
- No duplicate booking for the same customer/time
- Maximum booking horizon of 30 days
- No booking into past slots

The default seeded slot capacity is 6 per slot.

---

## Appointment check-in

Check-in opens:

```text
60 minutes before the appointment
```

and remains available until:

```text
30 minutes after the appointment ends
```

After that grace period, an appointment that was not checked in is marked:

```text
Missed
```

---

## Skipping customers

When a staff member skips a called customer, the token is moved to the end of the queue.

After `MAX_SKIPS` skips, the customer is marked as a no-show.

The default is:

```env
MAX_SKIPS=3
```

---

## Automatic queue cleanup

The backend periodically performs housekeeping.

It:

- Closes unfinished tokens from previous days
- Marks abandoned serving/waiting states appropriately
- Converts expired appointments to `Missed`
- Keeps appointment and ticket status synchronized where applicable

The sweep is throttled so it does not run unnecessarily on every request.

---

# Authentication and security

Authentication is handled by the FastAPI backend.

## Passwords

Passwords are hashed using bcrypt with a cost factor of 12.

Passwords are never stored in plaintext.

Input validation also enforces:

- Minimum password length: 8 characters
- Maximum password size: 72 bytes
- Basic email validation
- Phone number validation
- Name length/format validation

---

## JWT sessions

Successful authentication returns a JWT containing:

- User ID
- Role
- Expiration time

The frontend stores the token in browser `localStorage` and sends it as:

```http
Authorization: Bearer <access_token>
```

The backend validates the token before loading the current user and checking permissions.

If an account becomes inactive or archived, the backend rejects the session even if the JWT itself has not expired.

---

## Database access

The backend uses a PostgreSQL connection pool through `psycopg-pool`.

Transactions are handled through a shared context manager:

```python
with tx() as cur:
    ...
```

The database layer is configured for compatibility with Supabase's pooler by disabling prepared statements through:

```text
prepare_threshold=None
```

The application communicates directly with PostgreSQL rather than exposing database credentials to the frontend.

The supplied schema also enables PostgreSQL Row Level Security so the public Supabase REST interface cannot directly read/write the application's tables.

---

# API

All API endpoints are rooted at:

```text
/api
```

The complete OpenAPI specification is generated automatically by FastAPI and is available at:

```text
http://localhost:8000/docs
```

## Health and public endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Backend health check |
| GET | `/api/catalog` | Places, departments, services, and availability information |
| GET | `/api/public/stats` | Public organization statistics |
| GET | `/api/feedback` | Public feedback |
| POST | `/api/feedback` | Submit feedback |
| POST | `/api/contact` | Submit a contact message |

---

## Authentication endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create a customer account |
| POST | `/api/auth/login` | Authenticate and receive a JWT |
| GET | `/api/auth/me` | Return the current authenticated user |
| POST | `/api/auth/change-password` | Change the current password |
| POST | `/api/auth/forgot-password` | Password-reset request flow |

The forgot-password endpoint is intentionally generic and does not send an email/SMS by itself.

---

## Customer endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/departments/{department_id}/availability` | View appointment availability |
| POST | `/api/appointments` | Create an appointment |
| GET | `/api/appointments/{appt_id}` | View an appointment |
| PATCH | `/api/appointments/{appt_id}/reminder` | Update appointment reminder state |
| POST | `/api/appointments/{appt_id}/cancel` | Cancel an appointment |
| POST | `/api/appointments/{appt_id}/reschedule` | Reschedule an appointment |
| POST | `/api/appointments/{appt_id}/check-in` | Check in for an appointment |
| POST | `/api/queue/join` | Join a walk-in queue |
| GET | `/api/queue/my` | List the customer's active queue entries |
| GET | `/api/queue/tickets/{ticket_id}` | View a queue ticket and live ETA |
| POST | `/api/queue/tickets/{ticket_id}/cancel` | Leave a queue |
| GET | `/api/me/history` | View previous appointments/visits |
| GET | `/api/notifications` | List notifications |
| POST | `/api/notifications/read-all` | Mark all notifications as read |
| POST | `/api/notifications/{notification_id}/read` | Mark one notification as read |

---

## Staff endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/staff/console` | Current counter and waiting queue |
| GET | `/api/staff/tickets/{ticket_id}` | View customer/ticket details |
| POST | `/api/staff/call-next` | Call the next waiting token |
| POST | `/api/staff/recall` | Recall the current token |
| POST | `/api/staff/skip` | Skip the current token |
| POST | `/api/staff/start` | Start serving the current token |
| POST | `/api/staff/complete` | Complete service |
| PUT | `/api/staff/status` | Change counter status |

---

## Manager endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/manager/overview` | Department queue overview |
| GET | `/api/manager/departments` | Department information |
| GET | `/api/manager/staff` | List department staff |
| POST | `/api/manager/staff` | Add staff |
| DELETE | `/api/manager/staff/{staff_id}` | Remove staff |
| GET | `/api/manager/counters` | List service counters |
| POST | `/api/manager/counters` | Create a counter |
| DELETE | `/api/manager/counters/{counter_id}` | Remove a counter |
| PUT | `/api/manager/counters/{counter_id}/status` | Change counter status |
| PUT | `/api/manager/counters/{counter_id}/staff` | Assign/unassign counter staff |
| GET | `/api/manager/services` | List department services |
| POST | `/api/manager/services` | Create a service |
| PATCH | `/api/manager/services/{service_id}` | Update a service |
| DELETE | `/api/manager/services/{service_id}` | Archive a service |
| GET | `/api/manager/schedule` | View department schedule |
| PUT | `/api/manager/schedule` | Update schedule |
| GET | `/api/manager/reports` | Department statistics |

Managers are restricted to their own department. Administrators can provide a `department_id` when using department-scoped operations.

---

## Administrator endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/admin/departments` | List departments |
| POST | `/api/admin/departments` | Create a department |
| PATCH | `/api/admin/departments/{department_id}` | Update a department |
| DELETE | `/api/admin/departments/{department_id}` | Archive a department |
| GET | `/api/admin/users` | List users |
| POST | `/api/admin/users` | Create a user |
| PATCH | `/api/admin/users/{user_id}` | Update a user |
| DELETE | `/api/admin/users/{user_id}` | Remove/archive a user |
| GET | `/api/admin/services` | List services |
| POST | `/api/admin/services` | Create a service |
| PATCH | `/api/admin/services/{service_id}` | Update a service |
| DELETE | `/api/admin/services/{service_id}` | Archive a service |
| GET | `/api/admin/permissions` | View permission matrix |
| PUT | `/api/admin/permissions` | Change a role permission |
| GET | `/api/admin/activity` | Organization activity feed |
| GET | `/api/admin/reports` | Organization-wide analytics |
| GET | `/api/admin/reports.csv` | Export analytics as CSV |

---

# Frontend

The frontend deliberately avoids a JavaScript framework and is served directly by FastAPI.

## `index.html`

The main landing page contains:

- Product introduction
- Login
- Registration
- Customer portal
- Customer account information
- Notifications/history access
- Theme handling

---

## `place.html`

Provides the first stage of the customer workflow:

```text
Place → Department → Service
```

The seeded demo includes:

### University offices

- Examination
- Student Affairs
- Registrar
- Finance

### Banks

- Account Opening
- Cash Services
- Card Services
- Loans

### Clinics

- General Check-up
- Lab Tests
- Pharmacy
- Specialist Consultation

---

## `workflow.html`

Handles the actual customer transaction:

```text
Select service
      ↓
Appointment OR walk-in
      ↓
Choose slot / join queue
      ↓
Receive confirmation or token
      ↓
Track status
```

It supports appointment booking, appointment rescheduling, and walk-in queue entry.

---

## `staff.html`

The staff console is designed around the counter workflow:

```text
Available
   ↓
Call next
   ↓
Called
   ↓
Start service
   ↓
Serving
   ↓
Complete
```

A staff member can also recall or skip the active token.

---

## `manager.html`

The manager dashboard provides operational visibility into a single department, including:

- Current waiting customers
- Active counters
- Current ETA
- Today's appointments
- Staff
- Counters
- Services
- Schedule
- Reports

The page can poll for live queue information.

---

## `admin.html`

The administrator console provides organization-wide management:

- Departments
- Users
- Services
- Permissions
- Activity
- Reports
- CSV export

The visible tabs are filtered according to the current user's effective permissions.

---

# Demo data

The seeder creates three example environments:

```text
University offices
├── Examination
├── Student Affairs
├── Registrar
└── Finance

Banks
├── Account Opening
├── Cash Services
├── Card Services
└── Loans

Clinics
├── General Check-up
├── Lab Tests
├── Pharmacy
└── Specialist Consultation
```

It also generates historical and future data so that dashboards and reports are meaningful immediately after setup.

The generated data includes:

- Staff assignments
- Counters
- Services
- Appointment slots
- Completed tickets
- Waiting tickets
- Missed appointments
- Notifications
- Activity records
- Customer feedback

---

# Testing

The project includes an end-to-end smoke test:

```text
backend/tests/smoke_test.py
```

Run it from the `backend` directory:

```powershell
..\.venv\Scripts\python -m app.seed --reset
..\.venv\Scripts\python tests\smoke_test.py http://localhost:8000
```

The smoke test exercises the major application roles and permission boundaries, including:

- Authentication
- Customer workflows
- Queue operations
- Appointment operations
- Staff actions
- Manager actions
- Administrator actions
- Permission changes
- Department scoping
- User management
- Reports
- CSV export
- Activity logging
- Privilege-escalation protections

The test changes database state. Reseed the database afterward if you need the original demo state again.

A successful run ends with:

```text
ALL PASSED
```

---

# Error handling

FastAPI's application layer translates common PostgreSQL failures into user-facing HTTP responses.

Examples:

| Database condition | HTTP response |
|---|---|
| Unique constraint violation | `409 Conflict` |
| Foreign-key constraint violation | `409 Conflict` |
| Check constraint violation | `400 Bad Request` |
| Database connectivity failure | `503 Service Unavailable` |
| Missing/invalid authentication | `401 Unauthorized` |
| Missing permission | `403 Forbidden` |
| Missing resource | `404 Not Found` |

The frontend API client also converts FastAPI validation and error responses into readable messages.

---

# Time and timezone handling

flowQ uses a configured application timezone:

```env
APP_TIMEZONE=Asia/Karachi
```

The timezone is used for:

- Current date
- Queue dates
- Opening/closing hours
- Appointment slots
- Relative demo data
- Reports
- Displayed times

Time helpers are centralized in:

```text
backend/app/timeutil.py
```

This avoids scattering timezone logic across routers.

---

# Reports and analytics

The administrator and manager dashboards calculate operational metrics from real ticket and appointment timestamps.

Examples include:

- Visitors served
- Closed tickets
- Average wait
- Completion percentage
- Appointment no-show rate
- Queue length by hour
- Department waiting-time comparison
- Staff workload
- Current estimated wait
- Peak queue length

Administrator reports support:

```text
today
week
month
```

and can be exported as CSV.

---

# Notifications

flowQ stores application notifications in the database.

Examples include:

- Token called
- Turn approaching
- Appointment-related messages
- Queue status updates

The notification system is intentionally separated from delivery providers.

At the moment, notifications are displayed inside the application.

There is no built-in email or SMS delivery.

To add external delivery later, the notification hook in:

```text
backend/app/queue.py
```

can be connected to a provider such as Resend or Twilio.

---

# Current limitations

The current codebase intentionally leaves a few integrations as future work.

## Email/SMS delivery

Notifications are persisted and displayed in flowQ, but the application does not currently send email or SMS messages.

The forgot-password flow also does not send a real reset email.

---

## Nearby-location suggestions

The "Wait nearby" café/place suggestions on the walk-in experience use sample data.

A real maps/location provider would be required for live nearby-business results.

---

## CORS

The default configuration is permissive:

```env
CORS_ORIGINS=*
```

For production, restrict this to the actual frontend origin(s).

---

# Production considerations

This repository is structured as a functional application/demo rather than a complete production deployment recipe.

Before exposing it publicly, review at least:

### Secrets

- Set a strong persistent `JWT_SECRET`
- Keep `DATABASE_URL` out of source control
- Rotate any credentials that may have been used during development

### Demo accounts

- Remove or disable seeded demo accounts
- Replace the shared demo password
- Do not expose demo credentials in public deployments

### CORS

Replace:

```env
CORS_ORIGINS=*
```

with explicit trusted origins.

### HTTPS

Run the application behind HTTPS in production.

### Database

- Use a production Supabase/database project
- Back up the database
- Review connection pool sizing
- Restrict database credentials
- Review database RLS policies before deployment

### Frontend dependencies

The current frontend loads Tailwind, GSAP, Lenis, and fonts from external CDNs. For a controlled production environment, consider pinning and/or self-hosting these dependencies.

### Password recovery

Implement a real, expiring password-reset token flow with an email provider before treating password recovery as production-ready.

### Observability

Add structured application logging, error monitoring, and infrastructure health monitoring for production deployments.

---

# Development notes

## Core modules

### `main.py`

Creates the FastAPI application, registers middleware and exception handlers, mounts all routers, and serves the frontend.

### `db.py`

Owns the PostgreSQL connection pool and transaction context.

### `deps.py`

Centralizes authentication loading, permission checks, and department scoping.

### `permissions.py`

Defines roles and the permission catalogue.

### `queue.py`

Contains the core queue domain logic, ticket creation, ETA calculation, housekeeping, notifications, and activity logging.

### `security.py`

Contains bcrypt password hashing/verification and JWT creation/decoding.

### `ops.py`

Contains reusable service, counter, and statistics operations shared by managers and administrators.

### `seed.py`

Creates a realistic demo environment with deterministic/randomized sample data relative to the current application date.

### `timeutil.py`

Centralizes timezone-aware time and slot operations.

---

# Typical customer flow

```text
1. Open flowQ
       ↓
2. Log in / create customer account
       ↓
3. Choose a place
       ↓
4. Choose a department
       ↓
5. Choose a service
       ↓
6. Choose:
      ├── Appointment
      │      ↓
      │   Pick date/time
      │      ↓
      │   Confirm booking
      │
      └── Walk-in
             ↓
          Receive token
       ↓
7. Track queue position + ETA
       ↓
8. Receive "turn is close" notification
       ↓
9. Staff calls token
       ↓
10. Customer is served
       ↓
11. Ticket is completed
```

---

# Typical staff flow

```text
Staff logs in
    ↓
Counter status = Available
    ↓
Call next
    ↓
Customer status = Called
    ↓
Start
    ↓
Customer status = Serving
    ↓
Complete
    ↓
Counter becomes available again
```

If the customer is unavailable:

```text
Called
  ↓
Skip
  ↓
Token returns to queue
  ↓
After MAX_SKIPS
  ↓
No-show
```

---

# Typical manager flow

```text
Manager logs in
      ↓
Department overview
      ↓
Monitor queue
      ├── Waiting
      ├── Active counters
      ├── ETA
      └── Today's appointments
      ↓
Manage staff
      ↓
Manage counters
      ↓
Manage services
      ↓
Configure schedule
      ↓
Review department reports
```

---

# Typical administrator flow

```text
Administrator
      ↓
Departments
      ↓
Users
      ↓
Services
      ↓
Permissions
      ↓
Activity
      ↓
Reports
      ↓
CSV export
```

---


