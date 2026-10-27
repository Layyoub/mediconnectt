"""Turn a doctor's weekly availabilities into bookable time slots."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.utils import timezone

from .models import Appointment

BOOKING_DAYS = 14          # how far ahead patients can book
MIN_NOTICE = timedelta(hours=1)  # no booking less than 1 h ahead


@dataclass
class DaySlots:
    day: date
    slots: list = field(default_factory=list)   # aware datetimes


def _taken_starts(doctor, start, end):
    return set(
        Appointment.objects.filter(
            doctor=doctor, status__in=Appointment.ACTIVE_STATUSES, start__gte=start, start__lt=end
        ).values_list("start", flat=True)
    )


def available_slots(doctor, days=BOOKING_DAYS, now=None):
    """Free slots for the next `days` days, grouped by day (days without slots are skipped).

    Each slot is a tuple (start, end) of aware datetimes in the local time zone.
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    window_end = timezone.make_aware(datetime.combine(today + timedelta(days=days), datetime.min.time()))
    taken = _taken_starts(doctor, now, window_end)
    rules = list(doctor.availabilities.filter(is_active=True))

    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        day_slots = DaySlots(day)
        for rule in (r for r in rules if r.weekday == day.weekday()):
            step = timedelta(minutes=rule.slot_minutes)
            current = timezone.make_aware(datetime.combine(day, rule.start_time))
            limit = timezone.make_aware(datetime.combine(day, rule.end_time))
            while current + step <= limit:
                if current >= now + MIN_NOTICE and current not in taken:
                    day_slots.slots.append((current, current + step))
                current += step
        if day_slots.slots:
            day_slots.slots.sort()
            result.append(day_slots)
    return result


def find_slot(doctor, start, now=None):
    """Return (start, end) if `start` is a currently free slot of this doctor, else None."""
    for day in available_slots(doctor, now=now):
        for slot in day.slots:
            if slot[0] == start:
                return slot
    return None


def next_slot(doctor, now=None):
    days = available_slots(doctor, now=now)
    return days[0].slots[0] if days else None
