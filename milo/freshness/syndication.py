"""RSS and Atom parsing for freshness sources."""

import datetime as dt
import email.utils
import xml.etree.ElementTree as ET

from .util import as_text, collapse


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element):
    return collapse(" ".join(element.itertext()))


def _first_text(element, names):
    wanted = set(names)
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child.tag) in wanted:
            value = _element_text(child)
            if value:
                return value
    return ""


def _parse_time(value):
    raw = collapse(value)
    if not raw:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_feed(payload, feed):
    """Parse RSS or Atom into normalized item dictionaries."""
    root = ET.fromstring(as_text(payload))
    item_name = "entry" if _local_name(root.tag) == "feed" else "item"
    items = []
    for element in root.iter():
        if _local_name(element.tag) != item_name:
            continue
        title = _first_text(element, ("title",))
        url = ""
        for child in element.iter():
            if _local_name(child.tag) != "link":
                continue
            candidate = child.attrib.get("href", "").strip() or _element_text(child)
            rel = child.attrib.get("rel", "alternate")
            if candidate and rel in ("alternate", ""):
                url = candidate
                break
            if candidate and not url:
                url = candidate
        if not url:
            guid = _first_text(element, ("guid", "id"))
            if guid.startswith(("http://", "https://")):
                url = guid
        if title and url:
            items.append({
                "source": feed.get("name", "Unknown"),
                "group": feed.get("group", "news"),
                "title": title,
                "url": url,
                "published": _parse_time(_first_text(element, ("published", "updated", "pubdate", "date"))),
                "summary": _first_text(element, ("encoded", "content", "summary", "description")),
            })
    return items
