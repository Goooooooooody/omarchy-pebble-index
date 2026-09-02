from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

SCHEDULE_PREFIX = re.compile(
    r"^(?:please\s+)?(?:schedule|add(?:\s+to(?:\s+my)?\s+calendar)?|put\s+(?:it\s+|this\s+)?on\s+my\s+calendar|"
    r"calendar(?:\s+add)?)\s+",
    re.IGNORECASE,
)
REMIND_PREFIX = re.compile(
    r"^(?:remind\s+me\s+(?:to\s+|that\s+)?|remember\s+to\s+|note\s+(?:that\s+)?)",
    re.IGNORECASE,
)
SPOKEN_PM = re.compile(r"\bp\s*\.\s*m\.?", re.IGNORECASE)
SPOKEN_AM = re.compile(r"\ba\s*\.\s*m\.?", re.IGNORECASE)
OCLOCK = re.compile(r"\b(\d{1,2})\s*o['’]?clock\b", re.IGNORECASE)
RELATIVE_LONG = re.compile(r"\bin\s+(\d+)\s+(days?|weeks?)\b", re.IGNORECASE)
WEEKDAY_RE = re.compile(
    r"\b(?:(?:on|this|next)\s+)?(" + "|".join(WEEKDAYS) + r")\b",
    re.IGNORECASE,
)
NEXT_WEEK = re.compile(r"\bnext\s+week\b", re.IGNORECASE)
DAY_WORD = re.compile(r"\b(today|tomorrow|tonight)\b", re.IGNORECASE)
PERIOD = re.compile(r"\b(this\s+)?(morning|afternoon|evening)\b", re.IGNORECASE)
MONTH_DAY = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(MONTHS) + r")\b",
    re.IGNORECASE,
)
THE_NTH = re.compile(r"\bon\s+the\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)
TIME_RE = re.compile(
    r"""
    \b
    (?:
        (?P<hms>\d{1,2}):(?P<mms>\d{2})(?:\s*(?P<ampm1>am|pm))?
      | (?P<h12>\d{1,2})\s*(?P<ampm2>am|pm)
      | (?:at\s+)(?P<hat>\d{1,2})(?::(?P<mat>\d{2}))?(?:\s*(?P<ampm3>am|pm))?
      | (?P<named>noon|midnight)
      | half\s+past\s+(?P<half>\d{1,2})
      | quarter\s+(?P<quarter>to|past)\s+(?P<qhour>\d{1,2})
    )
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)
DATEISH_HINT = re.compile(
    r"\b("
    r"today|tomorrow|tonight|next\s+\w+|"
    + "|".join(WEEKDAYS)
    + r"|in\s+\d+\s+(?:days?|weeks?)|"
    + "|".join(MONTHS)
    + r"|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)|at\s+\d{1,2}|o['’]?clock|"
    r"noon|midnight|this\s+(?:morning|afternoon|evening)"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class ParsedEvent:
    when: datetime
    title: str
    time_explicit: bool


def normalize_spoken(text: str) -> str:
    cleaned = text.replace("\u2019", "'")
    cleaned = SPOKEN_PM.sub("pm", cleaned)
    cleaned = SPOKEN_AM.sub("am", cleaned)
    cleaned = OCLOCK.sub(r"\1:00", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_event(text: str, recorded_at: datetime) -> ParsedEvent | None:
    """Resolve a spoken calendar phrase against recorded_at. None if it is not an event."""
    cleaned = normalize_spoken(text)
    if DATEISH_HINT.search(cleaned) is None and RELATIVE_LONG.search(cleaned) is None:
        return None

    time_match = TIME_RE.search(cleaned)
    hour, minute, time_explicit = _read_time(cleaned, time_match)
    date_value, date_explicit = _read_date(cleaned, recorded_at)
    period = _read_period(cleaned)

    if hour is None and period is not None:
        hour, minute = period
        time_explicit = True
    if hour is None and date_explicit and date_value is not None and date_value != recorded_at.date():
        hour, minute = 9, 0
    if hour is None:
        return None
    if date_value is None:
        date_value = recorded_at.date()
        date_explicit = False

    tz = recorded_at.tzinfo
    when = datetime(date_value.year, date_value.month, date_value.day, hour, minute, tzinfo=tz)
    if when <= recorded_at and not date_explicit:
        when = when + timedelta(days=1)
    title = event_title(cleaned)
    if not title:
        title = "Calendar event"
    return ParsedEvent(when=when, title=title, time_explicit=time_explicit)


def event_title(text: str) -> str:
    cleaned = normalize_spoken(text)
    cleaned = SCHEDULE_PREFIX.sub("", cleaned)
    cleaned = REMIND_PREFIX.sub("", cleaned)
    cleaned = RELATIVE_LONG.sub(" ", cleaned)
    cleaned = TIME_RE.sub(" ", cleaned)
    cleaned = WEEKDAY_RE.sub(" ", cleaned)
    cleaned = NEXT_WEEK.sub(" ", cleaned)
    cleaned = DAY_WORD.sub(" ", cleaned)
    cleaned = PERIOD.sub(" ", cleaned)
    cleaned = MONTH_DAY.sub(" ", cleaned)
    cleaned = DAY_MONTH.sub(" ", cleaned)
    cleaned = THE_NTH.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:on|at|for|from|to)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned


def _read_time(text: str, match: re.Match[str] | None) -> tuple[int | None, int, bool]:
    if match is None:
        return None, 0, False
    if match.group("named"):
        name = match.group("named").lower()
        return (12, 0, True) if name == "noon" else (0, 0, True)
    if match.group("half"):
        hour = _to_hour(int(match.group("half")), None, text, minutes=30)
        return hour, 30, True
    if match.group("qhour"):
        raw = int(match.group("qhour"))
        if match.group("quarter").lower() == "to":
            raw = raw - 1 if raw > 1 else 12
            return _to_hour(raw, None, text, minutes=45), 45, True
        return _to_hour(raw, None, text, minutes=15), 15, True
    if match.group("hms"):
        hour = int(match.group("hms"))
        minute = int(match.group("mms"))
        ampm = match.group("ampm1")
        return _to_hour(hour, ampm, text, minutes=minute), minute, True
    if match.group("h12"):
        hour = int(match.group("h12"))
        ampm = match.group("ampm2")
        return _to_hour(hour, ampm, text), 0, True
    if match.group("hat"):
        hour = int(match.group("hat"))
        minute = int(match.group("mat") or 0)
        ampm = match.group("ampm3")
        return _to_hour(hour, ampm, text, minutes=minute), minute, True
    return None, 0, False


def _to_hour(hour: int, ampm: str | None, text: str, minutes: int = 0) -> int:
    if hour == 24:
        return 0
    if ampm:
        stamp = ampm.lower().replace(".", "")
        if stamp == "pm":
            if hour < 12:
                return hour + 12
            return 12
        if hour == 12:
            return 0
        return hour
    period = _read_period(text)
    if period is not None:
        base = period[0]
        if 1 <= hour <= 12:
            if base >= 12 and hour < 12:
                return hour + 12
            if base < 12 and hour == 12:
                return 0
            return hour if hour != 12 or base >= 12 else hour
    return _guess_hour(hour, text)


def _guess_hour(hour: int, text: str) -> int:
    if hour > 12:
        return hour
    lowered = text.lower()
    if any(word in lowered for word in ("dinner", "tonight", "evening", "drinks")):
        return hour if hour >= 12 else hour + 12
    if any(word in lowered for word in ("lunch", "afternoon")):
        if hour == 12:
            return 12
        return hour + 12 if hour < 12 else hour
    if any(word in lowered for word in ("standup", "stand-up", "morning", "breakfast")):
        return 0 if hour == 12 else hour
    if 1 <= hour <= 6:
        return hour + 12
    if hour == 12:
        return 12
    return hour


def _read_period(text: str) -> tuple[int, int] | None:
    if re.search(r"\btonight\b", text, re.IGNORECASE):
        return 19, 0
    match = PERIOD.search(text)
    if match is None:
        return None
    name = match.group(2).lower()
    if name == "morning":
        return 9, 0
    if name == "afternoon":
        return 15, 0
    return 18, 0


def _read_date(text: str, recorded_at: datetime) -> tuple[date | None, bool]:
    tz_today = recorded_at.date()
    long_rel = RELATIVE_LONG.search(text)
    if long_rel:
        count = int(long_rel.group(1))
        unit = long_rel.group(2).lower()
        days = count * 7 if unit.startswith("week") else count
        return tz_today + timedelta(days=days), True
    month_day = MONTH_DAY.search(text)
    if month_day:
        month = MONTHS[month_day.group(1).lower()]
        day = int(month_day.group(2))
        return _upcoming_date(tz_today, month, day), True
    day_month = DAY_MONTH.search(text)
    if day_month:
        day = int(day_month.group(1))
        month = MONTHS[day_month.group(2).lower()]
        return _upcoming_date(tz_today, month, day), True
    if NEXT_WEEK.search(text):
        return tz_today + timedelta(days=7), True
    day_word = DAY_WORD.search(text)
    if day_word:
        word = day_word.group(1).lower()
        if word == "tomorrow":
            return tz_today + timedelta(days=1), True
        if word in {"today", "tonight"}:
            return tz_today, True
    weekday = WEEKDAY_RE.search(text)
    if weekday:
        name = weekday.group(1).lower()
        matched = weekday.group(0).lower()
        target = WEEKDAYS.index(name)
        delta = (target - tz_today.weekday()) % 7
        if delta == 0:
            delta = 0 if matched.startswith("this") else 7
        return tz_today + timedelta(days=delta), True
    return None, False


def _upcoming_date(today: date, month: int, day: int) -> date | None:
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate
