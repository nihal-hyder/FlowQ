"""Permission catalogue. Each permission maps to what a role may do (see the 'Main Users & Their Roles' table).

Administrators always have every permission. The other roles start with the defaults below and an
administrator can change them in the admin console (Manage permissions).
"""

ROLES = {
    "customer": "Customer / Visitor",
    "staff": "Service Staff",
    "manager": "Department Manager",
    "admin": "Administrator",
}

# code -> (label, role that owns it by default)
PERMISSIONS: dict[str, tuple[str, str]] = {
    # Customer / Visitor
    "book_appointments": ("Book, cancel and reschedule appointments", "customer"),
    "join_queue": ("Join a digital queue and receive a token", "customer"),
    "view_queue_position": ("View queue position and estimated waiting time", "customer"),
    "view_history": ("View previous appointments or visits", "customer"),
    "receive_notifications": ("Receive notifications", "customer"),
    # Service Staff
    "view_waiting_customers": ("View waiting customers", "staff"),
    "call_tokens": ("Call, recall and skip tokens", "staff"),
    "serve_customers": ("Start and complete service", "staff"),
    "view_customer_details": ("View customer appointment details", "staff"),
    "update_service_status": ("Update service status", "staff"),
    # Department Manager
    "manage_staff": ("Manage staff", "manager"),
    "manage_counters": ("Manage service counters", "manager"),
    "manage_department_services": ("Create department services", "manager"),
    "manage_schedule": ("Set working hours, appointment duration and daily limits", "manager"),
    "view_department_stats": ("Monitor queue length, staff workload and waiting times", "manager"),
    # Administrator
    "manage_departments": ("Manage departments", "admin"),
    "manage_users": ("Manage users", "admin"),
    "manage_services": ("Manage services", "admin"),
    "manage_permissions": ("Manage permissions", "admin"),
    "view_org_activity": ("View organization-wide activity", "admin"),
    "view_reports": ("View reports and analytics", "admin"),
}

# Can never be granted to a non-admin role (prevents privilege escalation).
ADMIN_ONLY = {"manage_permissions"}

DEFAULT_GRANTS = [(owner, code) for code, (_, owner) in PERMISSIONS.items()]


def label(code: str) -> str:
    return PERMISSIONS.get(code, (code, ""))[0]
