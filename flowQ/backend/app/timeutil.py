from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import settings

TZ = ZoneInfo(settings.app_timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now_local().date()


def minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def from_minutes(m: int) -> time:
    return time(m // 60, m % 60)


def local_dt(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=TZ)


def fmt_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def fmt_slot(start: time, end: time) -> str:
    return f"{fmt_time(start)} – {fmt_time(end)}"


def slots_for(open_t: time, close_t: time, slot_minutes: int) -> list[tuple[time, time]]:
    out, m, end = [], minutes(open_t), minutes(close_t)
    while m + slot_minutes <= end:
        out.append((from_minutes(m), from_minutes(m + slot_minutes)))
        m += slot_minutes
    return out


def mins_between(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return (b - a) / timedelta(minutes=1)
