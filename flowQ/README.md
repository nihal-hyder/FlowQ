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


