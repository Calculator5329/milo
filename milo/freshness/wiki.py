"""Wikipedia Current Events day-box parsing."""

from __future__ import annotations

import datetime as dt
import re
from html import unescape
from html.parser import HTMLParser

from .article import BLOCK_TAGS, DROP_TAGS, MAX_ARTICLE_CHARS
from .util import as_text, collapse, utc_now


WIKIPEDIA_CURRENT_EVENTS = "https://en.wikipedia.org/wiki/Portal:Current_events"
MONTHS = {name: number for number, name in enumerate(
    ("", "January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December")) if name}


def _date_from_id(value):
    clean = unescape(value or "").strip().replace("-", "_").replace(" ", "_")
    year_first = re.fullmatch(r"(\d{4})_([A-Za-z]+)_(\d{1,2})", clean)
    month_first = re.fullmatch(r"([A-Za-z]+)_(\d{1,2}),?_(\d{4})", clean)
    if year_first:
        year, month_name, day = year_first.groups()
    elif month_first:
        month_name, day, year = month_first.groups()
    else:
        return None
    try:
        return dt.date(int(year), MONTHS[month_name.capitalize()], int(day))
    except (KeyError, ValueError):
        return None


class _CurrentEventsParser(HTMLParser):
    def __init__(self, target_dates):
        super().__init__(convert_charrefs=True)
        self.target_dates = set(target_dates)
        self.active = None
        self.drop_depth = 0
        self.sections = {day: [] for day in target_dates}

    def handle_starttag(self, tag, attrs):
        marker = _date_from_id(dict(attrs).get("id"))
        if marker is not None:
            self.active = marker if marker in self.target_dates else None
        if tag.lower() in DROP_TAGS:
            self.drop_depth += 1
        elif self.active is not None and not self.drop_depth and tag.lower() in BLOCK_TAGS:
            self.sections[self.active].append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in DROP_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
        elif self.active is not None and not self.drop_depth and tag.lower() in BLOCK_TAGS:
            self.sections[self.active].append(" ")

    def handle_data(self, data):
        if self.active is not None and not self.drop_depth:
            self.sections[self.active].append(data)


def parse_current_events(page, now=None):
    """Return one document for today and yesterday from the portal day sections."""
    today = utc_now(now).date()
    targets = (today, today - dt.timedelta(days=1))
    parser = _CurrentEventsParser(targets)
    parser.feed(as_text(page))
    parser.close()
    documents = []
    for day in targets:
        body = collapse("".join(parser.sections[day]), MAX_ARTICLE_CHARS)
        if not body:
            continue
        published = dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).isoformat(timespec="seconds")
        anchor = f"{day.year}_{day.strftime('%B')}_{day.day}"
        documents.append({
            "title": f"Wikipedia Current Events for {day.isoformat()}",
            "url": f"{WIKIPEDIA_CURRENT_EVENTS}#{anchor}",
            "published": published,
            "text": body,
        })
    return documents
