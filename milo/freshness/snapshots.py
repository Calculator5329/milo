"""Weather and market snapshot parsing."""

import csv
import io
import json

from .article import MAX_ARTICLE_CHARS
from .util import as_text, collapse


WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
    82: "heavy rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "heavy thunderstorm with hail",
}


def _unit_value(values, units, key):
    value = values.get(key)
    if value is None:
        return None
    return f"{value} {units.get(key, '')}".strip()


def parse_weather(payload, lat, lon):
    """Turn an Open-Meteo response into compact current and forecast prose."""
    data = json.loads(as_text(payload))
    current = data.get("current") or {}
    current_units = data.get("current_units") or {}
    daily = data.get("daily") or {}
    daily_units = data.get("daily_units") or {}
    parts = [f"Location: {lat}, {lon}."]
    if float(lat) == 0.0 and float(lon) == 0.0:
        parts.append("These are placeholder coordinates. Set lat and lon in ~/.local/state/milo/freshness.json.")
    if current.get("time"):
        parts.append(f"Observation: {current['time']} ({data.get('timezone') or 'local time'}).")
    current_bits = []
    for key, prefix in (("temperature_2m", ""), ("apparent_temperature", "feels like "),
                        ("precipitation", "precipitation "), ("wind_speed_10m", "wind ")):
        value = _unit_value(current, current_units, key)
        if value:
            current_bits.append(prefix + value)
    code = current.get("weather_code")
    if code in WEATHER_CODES:
        current_bits.insert(2, WEATHER_CODES[code])
    if current_bits:
        parts.append("Current: " + ", ".join(current_bits) + ".")
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rain = daily.get("precipitation_probability_max") or []
    codes = daily.get("weather_code") or []
    forecasts = []
    for index, day in enumerate(dates[:3]):
        bits = []
        if index < len(lows) and index < len(highs):
            low_unit = daily_units.get("temperature_2m_min", "")
            high_unit = daily_units.get("temperature_2m_max", "")
            bits.append(f"low {lows[index]} {low_unit}, high {highs[index]} {high_unit}".strip())
        if index < len(codes) and codes[index] in WEATHER_CODES:
            bits.append(WEATHER_CODES[codes[index]])
        if index < len(rain):
            rain_unit = daily_units.get("precipitation_probability_max", "")
            bits.append(f"precipitation chance {rain[index]} {rain_unit}".strip())
        forecasts.append(f"{day}: " + ", ".join(bits))
    if forecasts:
        parts.append("Forecast: " + "; ".join(forecasts) + ".")
    return collapse(" ".join(parts), MAX_ARTICLE_CHARS)


def parse_stooq(payload, symbol=""):
    """Turn the first Stooq CSV quote into one readable line."""
    reader = csv.DictReader(io.StringIO(as_text(payload).lstrip("\ufeff")))
    row = next(reader, None)
    if not row:
        return ""
    normalized = {str(key).strip().lower(): str(value or "").strip() for key, value in row.items()}
    name = normalized.get("symbol") or symbol.upper()
    close = normalized.get("close", "")
    if not name or not close or close.upper() in {"N/D", "N/A", "NA"}:
        return ""
    when = " ".join(part for part in (normalized.get("date"), normalized.get("time")) if part)
    fields = []
    for label in ("open", "high", "low", "close", "volume"):
        value = normalized.get(label)
        if value and value.upper() not in {"N/D", "N/A", "NA"}:
            fields.append(f"{label} {value}")
    return f"{name} as of {when}: " + ", ".join(fields) + "."
