-- =====================================================================
-- flowQ · Digital Queue & Appointment Management System
-- Database schema for Supabase (PostgreSQL 15+)
--
-- Run this in the Supabase SQL editor (or let `python -m app.seed --reset`
-- run it for you). WARNING: the first block drops existing flowQ tables.
-- =====================================================================

drop table if exists activity_log, notifications, tickets, token_counters,
  appointments, counters, role_permissions, feedback, contact_messages,
  users, services, departments, places cascade;
drop sequence if exists appointment_code_seq;

-- ---------- organisation ----------
create table places (
  id                 serial primary key,
  slug               text not null unique,
  name               text not null,
  description        text not null default '',
  icon               text not null default '',
  appointment_prefix text not null,              -- UN / BK / CL
  sort_order         int  not null default 0
);

create table departments (
  id            serial primary key,
  place_id      int  not null references places(id),
  name          text not null,
  token_prefix  text not null default 'A' check (token_prefix ~ '^[A-Z]{1,2}$'),
  active        boolean not null default true,
  archived      boolean not null default false,
  open_time     time not null default '09:00',
  close_time    time not null default '17:00',
  slot_minutes  int  not null default 30  check (slot_minutes in (15, 30, 45, 60)),
  slot_capacity int  not null default 6   check (slot_capacity between 1 and 50),
  daily_limit   int  not null default 120 check (daily_limit between 10 and 500),
  created_at    timestamptz not null default now(),
  check (close_time > open_time)
);
create unique index departments_name_uq on departments (place_id, lower(name)) where not archived;

create table services (
  id            serial primary key,
  department_id int  not null references departments(id),
  name          text not null,
  avg_minutes   int  not null check (avg_minutes between 1 and 120),
  active        boolean not null default true,
  archived      boolean not null default false,
  created_at    timestamptz not null default now()
);
create unique index services_name_uq on services (department_id, lower(name)) where not archived;

-- ---------- people & access ----------
create table users (
  id            uuid primary key default gen_random_uuid(),
  full_name     text not null,
  email         text not null,
  phone         text,
  password_hash text not null,
  role          text not null check (role in ('customer', 'staff', 'manager', 'admin')),
  department_id int references departments(id),
  active        boolean not null default true,
  archived      boolean not null default false,
  created_at    timestamptz not null default now()
);
create unique index users_email_uq on users (lower(email)) where not archived;
create index users_department_idx on users (department_id);

create table role_permissions (
  role       text not null check (role in ('customer', 'staff', 'manager', 'admin')),
  permission text not null,
  primary key (role, permission)
);

create table counters (
  id            serial primary key,
  department_id int  not null references departments(id),
  name          text not null,
  status        text not null default 'Closed' check (status in ('Available', 'Busy', 'Break', 'Closed')),
  staff_id      uuid unique references users(id),
  created_at    timestamptz not null default now()
);
create index counters_department_idx on counters (department_id);

-- ---------- appointments & queue ----------
create sequence appointment_code_seq start 412;

create table appointments (
  id            uuid primary key default gen_random_uuid(),
  code          text not null unique,
  user_id       uuid not null references users(id),
  department_id int  not null references departments(id),
  service_id    int  not null references services(id),
  appt_date     date not null,
  slot_start    time not null,
  slot_end      time not null,
  status        text not null check (status in ('Booked', 'Confirmed', 'Rescheduled', 'Delayed', 'Checked In',
                                                'Waiting', 'In Service', 'Completed', 'Cancelled', 'Missed')),
  reminder      text check (reminder in ('Email', 'SMS', 'In-app alert')),
  checked_in_at timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index appointments_day_idx  on appointments (department_id, appt_date);
create index appointments_user_idx on appointments (user_id, appt_date desc);

-- one row per department per day; gives race-free token numbers (A-001, A-002 ...)
create table token_counters (
  department_id int  not null references departments(id),
  queue_date    date not null,
  last_number   int  not null,
  primary key (department_id, queue_date)
);

create table tickets (
  id              uuid primary key default gen_random_uuid(),
  department_id   int  not null references departments(id),
  service_id      int  not null references services(id),
  user_id         uuid references users(id),
  appointment_id  uuid unique references appointments(id),
  queue_date      date not null,
  number          int  not null,
  token           text not null,
  kind            text not null check (kind in ('walkin', 'appointment')),
  status          text not null check (status in ('waiting', 'called', 'serving', 'completed', 'cancelled', 'no_show')),
  counter_id      int  references counters(id) on delete set null,
  counter_name    text,
  staff_id        uuid references users(id),
  sort_at         timestamptz not null default now(),   -- queue order; a skip moves it to the end
  skip_count      int  not null default 0,
  recall_count    int  not null default 0,
  near_notified   boolean not null default false,
  created_at      timestamptz not null default now(),
  first_called_at timestamptz,
  called_at       timestamptz,
  started_at      timestamptz,
  closed_at       timestamptz,
  unique (department_id, queue_date, number)
);
create index tickets_queue_idx on tickets (department_id, queue_date, status, sort_at);
create index tickets_user_idx  on tickets (user_id, created_at desc);
create index tickets_staff_idx on tickets (staff_id, queue_date);

-- ---------- messages, activity, public ----------
create table notifications (
  id         bigserial primary key,
  user_id    uuid not null references users(id),
  title      text not null,
  body       text not null default '',
  read       boolean not null default false,
  created_at timestamptz not null default now()
);
create index notifications_user_idx on notifications (user_id, created_at desc);

create table activity_log (
  id            bigserial primary key,
  category      text not null check (category in ('Departments', 'Users', 'Services', 'Permissions', 'Queue')),
  message       text not null,
  event         text,                         -- called / skipped / completed ... (queue events)
  actor_id      uuid references users(id),
  department_id int  references departments(id),
  ticket_id     uuid references tickets(id) on delete set null,
  created_at    timestamptz not null default now()
);
create index activity_created_idx on activity_log (created_at desc);
create index activity_actor_idx   on activity_log (actor_id, created_at desc);

create table feedback (
  id         bigserial primary key,
  user_id    uuid references users(id),
  name       text not null,
  rating     int  not null check (rating between 1 and 5),
  comment    text not null,
  context    text not null default '',
  created_at timestamptz not null default now()
);

create table contact_messages (
  id         bigserial primary key,
  name       text not null,
  email      text not null,
  phone      text not null,
  message    text not null,
  created_at timestamptz not null default now()
);

-- ---------- security ----------
-- All access goes through the FastAPI backend (it connects as the table owner).
-- Row Level Security with no policies blocks Supabase's public REST API (anon /
-- authenticated keys) from reading or writing these tables directly.
alter table places            enable row level security;
alter table departments       enable row level security;
alter table services          enable row level security;
alter table users             enable row level security;
alter table role_permissions  enable row level security;
alter table counters          enable row level security;
alter table appointments      enable row level security;
alter table token_counters    enable row level security;
alter table tickets           enable row level security;
alter table notifications     enable row level security;
alter table activity_log      enable row level security;
alter table feedback          enable row level security;
alter table contact_messages  enable row level security;
