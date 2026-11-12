"""Small formatting and date helpers shared by views and routes."""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Iterable

PT_MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

PT_MONTHS_SHORT = [
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
]

WEEKDAYS_SHORT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def eur(value: float | int | None, decimals: int = 2, sign: bool = False) -> str:
    """Format a number like a Portuguese euro amount: 1 234,56 €."""
    value = float(value or 0)
    prefix = ""
    if sign and value > 0:
        prefix = "+"
    if value < 0:
        prefix = "−"
        value = abs(value)
    formatted = f"{value:,.{decimals}f}".replace(",", "\u0000").replace(".", ",").replace("\u0000", "\u00a0")
    return f"{prefix}{formatted}\u00a0€"


def eur_compact(value: float | int | None) -> str:
    value = float(value or 0)
    if abs(value) >= 1000:
        return f"{eur(value / 1000, 1)}k"
    return eur(value, 0)


def parse_money(value: str | float | int | None, default: float = 0.0) -> float:
    """Accept 1 300,00 / 1300.00 / 1300,00 / 1.300,00 / empty."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("€", "").replace("\u00a0", "").replace(" ", "")
    if not raw:
        return default
    negative = raw.startswith("-") or raw.startswith("−")
    raw = raw.lstrip("+-−")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif "." in raw and len(raw.rsplit(".", 1)[-1]) == 3:
        # "1.300" in Portuguese means 1300
        raw = raw.replace(".", "")
    try:
        number = float(raw)
    except ValueError:
        return default
    return -number if negative else number


def parse_int(value: str | int | None, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def pct(part: float | int | None, whole: float | int | None, decimals: int = 0) -> float:
    whole = float(whole or 0)
    if whole == 0:
        return 0.0
    return round(float(part or 0) / whole * 100, decimals)


def pct_pt(value: float | int | None, decimals: int = 1) -> str:
    """Portuguese decimal comma for percentages: 9.15 -> 9,15."""
    try:
        return f"{float(value or 0):.{decimals}f}".replace(".", ",")
    except (TypeError, ValueError):
        return "0"


def safe_div(part: float | int | None, whole: float | int | None) -> float:
    whole = float(whole or 0)
    return float(part or 0) / whole if whole else 0.0


def month_label(month: str, short: bool = False, year: bool = True) -> str:
    """2026-10 -> outubro 2026 / out 26."""
    try:
        y, m = month.split("-")
        idx = int(m) - 1
        name = PT_MONTHS_SHORT[idx] if short else PT_MONTHS[idx]
        return f"{name} {y}" if year else name
    except (ValueError, IndexError):
        return month


def month_short(month: str) -> str:
    try:
        y, m = month.split("-")
        return f"{PT_MONTHS_SHORT[int(m) - 1]} {y[2:]}"
    except (ValueError, IndexError):
        return month


def month_display(month: str) -> str:
    label = month_label(month)
    return label[:1].upper() + label[1:]


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def month_add(month: str, delta: int) -> str:
    year, mon = (int(x) for x in month.split("-"))
    idx = year * 12 + (mon - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_range(end_month: str, count: int) -> list[str]:
    return [month_add(end_month, -i) for i in range(count - 1, -1, -1)]


def today_iso() -> str:
    return date.today().isoformat()


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def date_label(value: str | None) -> str:
    d = parse_iso(value)
    if not d:
        return "—"
    return f"{d.day:02d} {PT_MONTHS_SHORT[d.month - 1]}"


def date_full(value: str | None) -> str:
    d = parse_iso(value)
    if not d:
        return "—"
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def weekday_label(value: str | None) -> str:
    d = parse_iso(value)
    if not d:
        return ""
    return WEEKDAYS_SHORT[d.weekday()]


def days_until(value: str | None) -> int | None:
    d = parse_iso(value)
    if not d:
        return None
    return (d - date.today()).days


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "item"


def conic_gradient(items: Iterable[dict], color_key: str = "color", share_key: str = "share") -> str:
    """Build a CSS conic-gradient from category totals for the dashboard donut."""
    items = list(items)
    if not items:
        return "conic-gradient(#e7dbc6 0% 100%)"
    stops: list[str] = []
    accumulated = 0.0
    for item in items:
        start = accumulated
        accumulated += float(item.get(share_key) or 0)
        color = item.get(color_key) or "#8C7A5E"
        stops.append(f"{color} {start:.2f}% {accumulated:.2f}%")
    if accumulated < 100:
        stops.append(f"#e7dbc6 {accumulated:.2f}% 100%")
    return f"conic-gradient({', '.join(stops)})"


def cadence_label(cadence: str) -> str:
    return {
        "weekly": "semanal",
        "monthly": "mensal",
        "quarterly": "trimestral",
        "yearly": "anual",
    }.get(cadence, cadence)


def monthly_equivalent(amount: float, cadence: str) -> float:
    if cadence == "weekly":
        return amount * 52 / 12
    if cadence == "quarterly":
        return amount / 3
    if cadence == "yearly":
        return amount / 12
    return amount


def annual_equivalent(amount: float, cadence: str) -> float:
    if cadence == "weekly":
        return amount * 52
    if cadence == "quarterly":
        return amount * 4
    if cadence == "yearly":
        return amount
    return amount * 12


def add_cadence(value: str | None, cadence: str) -> str | None:
    base = parse_iso(value) or date.today()
    if cadence == "weekly":
        nxt = base + timedelta(days=7)
    elif cadence == "quarterly":
        month = base.month + 3
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(base.day, calendar.monthrange(year, month)[1])
        nxt = date(year, month, day)
    elif cadence == "yearly":
        year = base.year + 1
        day = min(base.day, calendar.monthrange(year, base.month)[1])
        nxt = date(year, base.month, day)
    else:
        month = base.month + 1
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(base.day, calendar.monthrange(year, month)[1])
        nxt = date(year, month, day)
    return nxt.isoformat()


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def sum_amounts(rows: Iterable[dict], key: str = "amount") -> float:
    return sum(float(r.get(key) or 0) for r in rows)


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def group_label(group: str) -> str:
    return {
        "income": "Rendimento",
        "essentials": "Essenciais",
        "lifestyle": "Estilo de vida",
        "future": "Futuro",
        "other": "Outros",
    }.get(group, group)
