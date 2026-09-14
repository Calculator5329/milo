"""Small normalization helpers shared by freshness modules."""

from __future__ import annotations

import datetime as dt
from html import unescape


def utc_now(value=None):
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def iso_time(value=None):
    return utc_now(value).isoformat(timespec="seconds")


def as_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def collapse(value, limit=None):
    clean = " ".join(unescape(value or "").split())
    return clean[:limit] if limit is not None else clean
