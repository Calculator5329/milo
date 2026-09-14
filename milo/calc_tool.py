"""Small, deterministic calculator and conversion tool for Milo."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RATES_AS_OF = "2026-01-01"
STATIC_CURRENCY_RATES = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 149.0,
    "CAD": 1.36,
    "AUD": 1.52,
    "CHF": 0.88,
    "CNY": 7.24,
    "INR": 83.1,
    "MXN": 18.1,
    "NZD": 1.67,
    "SEK": 10.6,
    "NOK": 10.7,
    "DKK": 6.87,
    "SGD": 1.34,
    "HKD": 7.82,
    "KRW": 1335.0,
    "BRL": 5.0,
    "ZAR": 18.0,
}


@dataclass(frozen=True)
class Unit:
    dimension: str
    factor: float
    label: str


def _units() -> dict[str, Unit]:
    values: dict[str, Unit] = {}

    def add(dimension: str, factor: float, label: str, *aliases: str) -> None:
        unit = Unit(dimension, factor, label)
        for alias in aliases:
            values[alias] = unit

    add("length", 0.001, "millimeters", "mm", "millimeter", "millimeters", "millimetre", "millimetres")
    add("length", 0.01, "centimeters", "cm", "centimeter", "centimeters", "centimetre", "centimetres")
    add("length", 1.0, "meters", "m", "meter", "meters", "metre", "metres")
    add("length", 1000.0, "kilometers", "km", "kilometer", "kilometers", "kilometre", "kilometres")
    add("length", 0.0254, "inches", "in", "inch", "inches")
    add("length", 0.3048, "feet", "ft", "foot", "feet")
    add("length", 0.9144, "yards", "yd", "yard", "yards")
    add("length", 1609.344, "miles", "mi", "mile", "miles")

    add("mass", 0.001, "grams", "g", "gram", "grams")
    add("mass", 1.0, "kilograms", "kg", "kilogram", "kilograms")
    add("mass", 0.028349523125, "ounces", "oz", "ounce", "ounces")
    add("mass", 0.45359237, "pounds", "lb", "lbs", "pound", "pounds")
    add("mass", 6.35029318, "stones", "stone", "stones")

    add("temperature", 1.0, "degrees Celsius", "c", "celsius", "degree c", "degrees c")
    add("temperature", 1.0, "degrees Fahrenheit", "f", "fahrenheit", "degree f", "degrees f")
    add("temperature", 1.0, "kelvins", "k", "kelvin", "kelvins")

    add("volume", 1.0, "milliliters", "ml", "milliliter", "milliliters")
    add("volume", 1000.0, "liters", "l", "liter", "liters", "litre", "litres")
    add("volume", 236.5882365, "cups", "cup", "cups")
    add("volume", 473.176473, "pints", "pint", "pints")
    add("volume", 946.352946, "quarts", "quart", "quarts")
    add("volume", 3785.411784, "gallons", "gallon", "gallons")
    add("volume", 4.92892159375, "teaspoons", "tsp", "teaspoon", "teaspoons")
    add("volume", 14.78676478125, "tablespoons", "tbsp", "tablespoon", "tablespoons")
    add("volume", 29.5735295625, "fluid ounces", "fl oz", "fluid ounce", "fluid ounces")

    add("speed", 1.0, "meters per second", "m/s", "m / s", "meter per second", "meters per second")
    add("speed", 1000.0 / 3600.0, "kilometers per hour", "km/h", "km / h", "kph", "kilometer per hour", "kilometers per hour")
    add("speed", 1609.344 / 3600.0, "miles per hour", "mph", "mile per hour", "miles per hour")
    add("speed", 0.514444444444, "knots", "knot", "knots")

    for power, decimal_label, binary_label in (
        (0, "bytes", "bytes"),
        (1, "kilobytes", "kibibytes"),
        (2, "megabytes", "mebibytes"),
        (3, "gigabytes", "gibibytes"),
        (4, "terabytes", "tebibytes"),
    ):
        decimal_factor = 1000.0 ** power
        binary_factor = 1024.0 ** power
        decimal_code = "B" if power == 0 else "".join(("K", "M", "G", "T")[power - 1:power]) + "B"
        binary_code = "B" if power == 0 else "".join(("K", "M", "G", "T")[power - 1:power]) + "iB"
        add("data", decimal_factor, decimal_label, decimal_code.lower(), decimal_label[:-1], decimal_label)
        add("data", binary_factor, binary_label, binary_code.lower(), binary_label[:-1], binary_label)

    add("time", 1.0, "seconds", "s", "sec", "second", "seconds")
    add("time", 60.0, "minutes", "min", "minute", "minutes")
    add("time", 3600.0, "hours", "hr", "hour", "hours")
    add("time", 86400.0, "days", "day", "days")
    add("time", 604800.0, "weeks", "week", "weeks")
    add("time", 2629800.0, "months", "month", "months")
    add("time", 31557600.0, "years", "year", "years")

    add("area", 1.0, "square meters", "sq m", "square meter", "square meters", "sqm")
    add("area", 0.09290304, "square feet", "sq ft", "square foot", "square feet", "sqft")
    add("area", 4046.8564224, "acres", "acre", "acres")
    add("area", 10000.0, "hectares", "hectare", "hectares")
    return values


UNITS = _units()
UNIT_ALIASES = sorted(UNITS, key=len, reverse=True)

CURRENCY_NAMES = {
    "USD": ("dollar", "dollars", "dollar", "dollars", "$"),
    "EUR": ("euro", "euros", "euro", "euros", "€"),
    "GBP": ("pound", "pounds", "pound", "pounds", "£"),
    "JPY": ("yen", "yen", "yen", "yen", "¥"),
    "CAD": ("Canadian dollar", "Canadian dollars", "Canadian dollar", "Canadian dollars", "C$"),
    "AUD": ("Australian dollar", "Australian dollars", "Australian dollar", "Australian dollars", "A$"),
    "CHF": ("Swiss franc", "Swiss francs", "Swiss franc", "Swiss francs", "CHF"),
    "CNY": ("Chinese yuan", "Chinese yuan", "Chinese yuan", "Chinese yuan", "元"),
    "INR": ("Indian rupee", "Indian rupees", "Indian rupee", "Indian rupees", "₹"),
    "MXN": ("Mexican peso", "Mexican pesos", "Mexican peso", "Mexican pesos", "MX$"),
    "NZD": ("New Zealand dollar", "New Zealand dollars", "New Zealand dollar", "New Zealand dollars", "NZ$"),
    "SEK": ("Swedish krona", "Swedish kronor", "Swedish krona", "Swedish kronor", "kr"),
    "NOK": ("Norwegian krone", "Norwegian kroner", "Norwegian krone", "Norwegian kroner", "kr"),
    "DKK": ("Danish krone", "Danish kroner", "Danish krone", "Danish kroner", "kr"),
    "SGD": ("Singapore dollar", "Singapore dollars", "Singapore dollar", "Singapore dollars", "S$"),
    "HKD": ("Hong Kong dollar", "Hong Kong dollars", "Hong Kong dollar", "Hong Kong dollars", "HK$"),
    "KRW": ("South Korean won", "South Korean won", "South Korean won", "South Korean won", "₩"),
    "BRL": ("Brazilian real", "Brazilian reals", "Brazilian real", "Brazilian reals", "R$"),
    "ZAR": ("South African rand", "South African rand", "South African rand", "South African rand", "R"),
}
CURRENCY_ALIASES: dict[str, str] = {}
for _code, _names in CURRENCY_NAMES.items():
    CURRENCY_ALIASES[_code.lower()] = _code
    for _name in _names:
        CURRENCY_ALIASES[_name.lower()] = _code
CURRENCY_ALIASES.update({
    "us dollar": "USD", "us dollars": "USD", "american dollar": "USD", "american dollars": "USD",
    "uk pound": "GBP", "uk pounds": "GBP",
})


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMBER_WORD_SET = set(NUMBER_WORDS) | {"hundred", "and"}


def _consume_number_words(tokens: list[str], start: int) -> tuple[int, int] | None:
    if start >= len(tokens) or tokens[start] not in NUMBER_WORDS:
        return None
    total = 0
    current = 0
    index = start
    saw_hundred = False
    while index < len(tokens):
        token = tokens[index]
        if token in NUMBER_WORDS:
            current += NUMBER_WORDS[token]
            index += 1
        elif token == "hundred" and current and not saw_hundred:
            total += current * 100
            current = 0
            saw_hundred = True
            index += 1
        elif token == "and" and index + 1 < len(tokens) and tokens[index + 1] in _NUMBER_WORD_SET:
            index += 1
        else:
            break
    if index == start:
        return None
    return total + current, index


def _normalise_text(text: str) -> str:
    text = text.strip().lower().replace("’", "'")
    for symbol, code in (("$", "usd"), ("€", "eur"), ("£", "gbp"), ("¥", "jpy"), ("₹", "inr")):
        text = re.sub(rf"{re.escape(symbol)}\s*(\d+(?:\.\d+)?)", rf"\1 {code}", text)
        text = text.replace(symbol, f" {code} ")
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    text = text.replace("'", "")
    tokens = re.findall(r"\d+(?:\.\d+)?|\.\d+|[a-z]+|\*\*|[*/()+%,-]", text)
    output: list[str] = []
    index = 0
    while index < len(tokens):
        consumed = _consume_number_words(tokens, index)
        if consumed:
            value, end = consumed
            output.append(str(value))
            index = end
        else:
            output.append(tokens[index])
            index += 1
    return " ".join(output)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    if isinstance(value, int) or value.is_integer():
        return int(value)
    magnitude = abs(float(value))
    places = 4 - math.floor(math.log10(magnitude)) - 1 if magnitude else 4
    rounded = round(float(value), places)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _number_text(value: int | float) -> str:
    if value < 0:
        return f"negative {_number_text(-value)}"
    if isinstance(value, int):
        return str(value)
    text = format(value, ".4g")
    if "e" in text:
        places = max(0, 4 - math.floor(math.log10(value)) - 1)
        text = format(value, f".{places}f").rstrip("0").rstrip(".")
    return text


def _evaluate_ast(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        if not math.isfinite(float(node.value)):
            raise ValueError("non-finite number")
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _evaluate_ast(node.operand)
        return -value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)):
        left = _evaluate_ast(node.left)
        right = _evaluate_ast(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 64:
            raise ValueError("exponent too large")
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("division by zero")
        if isinstance(node.op, ast.Mod) and right == 0:
            raise ValueError("modulo by zero")
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
        return left % right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1 and not node.keywords:
        return math.sqrt(_evaluate_ast(node.args[0]))
    raise ValueError("unsupported expression")


def _evaluate_expression(expression: str) -> int | float | None:
    try:
        tree = ast.parse(expression, mode="eval")
        return _number(_evaluate_ast(tree))
    except (ArithmeticError, SyntaxError, ValueError, TypeError, OverflowError, MemoryError):
        return None


def _pretty_expression(expression: str) -> str:
    expression = re.sub(r"\bsqrt\s*\(\s*([^()]+)\s*\)", r"square root of \1", expression)
    expression = expression.replace("**", " to the power of ")
    expression = expression.replace("*", " times ")
    expression = expression.replace("/", " divided by ")
    expression = expression.replace("+", " plus ")
    expression = expression.replace("%", " modulo ")
    expression = re.sub(r"(^|[(*\/+%])\s*-\s*(\d+(?:\.\d+)?)", r"\1 negative \2", expression)
    expression = expression.replace("-", " minus ")
    expression = expression.replace("(", " open parenthesis ").replace(")", " close parenthesis ")
    return re.sub(r"\s+", " ", expression).strip()


_ASK_PREFIX = re.compile(
    r"^(?:(?:hey |ok |okay )?milo,?\s+)?(?:please\s+)?(?:(?:can|could|would) you (?:please )?)?"
    r"(?:(?:tell me|work out|figure out|calculate|compute)\s+)?(?:what is|whats|what s|how much is|what does)?\s*(?:the\s+)?"
)


def _percent_off(normalized: str) -> dict[str, Any] | None:
    """'20 percent off of 150 dollars' -> 120 dollars; the unit word, if any, is kept for speech."""
    match = re.fullmatch(
        r"(-?\d+(?:\.\d+)?)\s*(?:%|percent|percentage)\s+off(?:\s+of)?\s+(-?\d+(?:\.\d+)?)(?:\s+([a-z]+))?",
        normalized)
    if not match:
        return None
    percent_value = float(match.group(1))
    base = float(match.group(2))
    result = _number(base * (1 - percent_value / 100))
    if result is None:
        return None
    unit = f" {match.group(3)}" if match.group(3) else ""
    left = _number(percent_value)
    right = _number(base)
    return {"kind": "percent", "expression": f"{left} percent off {right}{unit}", "result": result,
            "spoken": f"{left} percent off {_number_text(right)}{unit} is {_number_text(result)}{unit}"}


def _arithmetic(text: str) -> dict[str, Any] | None:
    normalized = _normalise_text(text).rstrip(" ?.!?")
    normalized = _ASK_PREFIX.sub("", normalized, count=1).strip()
    off = _percent_off(normalized)
    if off:
        return off
    percent = re.fullmatch(r"(?:(?:what is|whats|calculate|compute)\s+)?(-?\d+(?:\.\d+)?)\s*(?:%|percent|percentage)\s+of\s+(-?\d+(?:\.\d+)?)", normalized)
    if percent:
        percent_value = float(percent.group(1))
        base = float(percent.group(2))
        result = _number(percent_value * base / 100)
        if result is None:
            return None
        left = _number(float(percent.group(1)))
        right = _number(float(percent.group(2)))
        return {"kind": "percent", "expression": f"{left} percent of {right}", "result": result,
                "spoken": f"{left} percent of {right} is {_number_text(result)}"}

    root = re.fullmatch(r"(?:(?:what is|whats|calculate|compute)\s+)?square root of\s+(.+)", normalized)
    display = None
    if root:
        argument = root.group(1)
        expression = f"sqrt({argument})"
        display = f"The square root of {argument}"
    else:
        expression = re.sub(r"^(?:please\s+)?(?:what is|whats|calculate|compute)\s+", "", normalized)
        expression = re.sub(r"\bmultiplied by\b|\btimes\b", "*", expression)
        expression = re.sub(r"\bdivided by\b|\bover\b", "/", expression)
        expression = re.sub(r"\bto the power of\b|\braised to the power of\b", "**", expression)
        expression = re.sub(r"\bplus\b", "+", expression)
        expression = re.sub(r"\bminus\b", "-", expression)
        expression = re.sub(r"\bmodulo\b|\bmod\b", "%", expression)
        expression = expression.replace("open parenthesis", "(").replace("close parenthesis", ")")
        expression = expression.strip()
        if not re.search(r"\d|sqrt|[()+\-*/%]", expression):
            return None
    result = _evaluate_expression(expression)
    if result is None:
        return None
    display = display or _pretty_expression(expression)
    return {"kind": "arithmetic", "expression": expression, "result": result,
            "spoken": f"{display} is {_number_text(result)}"}


def _unit_key(value: str) -> str:
    value = value.lower().strip().rstrip("?.!,")
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\s+", " ", value)
    return value


def _find_unit(value: str) -> str | None:
    return _unit_key(value) if _unit_key(value) in UNITS else None


_UNIT_PATTERN = "|".join(re.escape(alias).replace(r"\ ", r"\s+") for alias in UNIT_ALIASES)
_MEASUREMENT_RE = re.compile(rf"(?<!\w)([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>{_UNIT_PATTERN})(?!\w)")


def _parse_measurements(source: str) -> tuple[list[tuple[float, str]], str] | None:
    matches = list(_MEASUREMENT_RE.finditer(source))
    if not matches:
        unit = _find_unit(source)
        if unit and re.fullmatch(r"(?:a|an|one)\s+.*", source):
            return [(1.0, unit)], f"1 {unit}"
        return None
    pieces: list[tuple[float, str]] = []
    cursor = 0
    for match in matches:
        remainder = source[cursor:match.start()].strip()
        if remainder and remainder not in {"and", ","}:
            return None
        pieces.append((float(match.group(1)), _unit_key(match.group("unit"))))
        cursor = match.end()
    if source[cursor:].strip():
        return None
    return pieces, source.strip()


def _temperature_to_base(value: float, unit: str) -> float:
    if unit in {"f", "fahrenheit", "degree f", "degrees f"}:
        return (value - 32.0) * 5.0 / 9.0
    if unit in {"k", "kelvin", "kelvins"}:
        return value - 273.15
    return value


def _temperature_from_base(value: float, unit: str) -> float:
    if unit in {"f", "fahrenheit", "degree f", "degrees f"}:
        return value * 9.0 / 5.0 + 32.0
    if unit in {"k", "kelvin", "kelvins"}:
        return value + 273.15
    return value


def _split_conversion(text: str) -> tuple[str, str] | None:
    match = re.match(r"^(.*?)\s+(?:to|into|in)\s+(.+?)\s*$", text)
    return (match.group(1).strip(), match.group(2).strip()) if match else None


def _unit_answer(text: str) -> dict[str, Any] | None:
    normalized = _normalise_text(text).rstrip(" ?.!?")
    if normalized.startswith("how many "):
        measurements = []
        worded = re.fullmatch(r"how many (.+?) (?:is|are|make|makes|equal|equals)(?: there)?(?: in)? (.+)", normalized)
        split = (worded.group(1), worded.group(2)) if worded else _split_conversion(normalized[9:])
        if split:
            destination_text, source_text = split
            destination = _find_unit(destination_text)
            source_text = re.sub(r"^(?:a|an|one)\s+", "1 ", source_text)
            parsed = _parse_measurements(source_text)
            if destination and parsed:
                measurements, source_display = parsed
                target = destination
            else:
                source = _find_unit(source_text)
                if destination and source and UNITS[destination].dimension == UNITS[source].dimension:
                    measurements = [(1.0, source)]
                    source_display = f"1 {source}"
                    target = destination
    else:
        normalized = re.sub(r"^(?:please\s+)?(?:convert|what is|whats|calculate|compute)\s+", "", normalized)
        normalized = _ASK_PREFIX.sub("", normalized, count=1).strip()
        split = _split_conversion(normalized)
        if not split:
            return None
        source_text, target_text = split
        target = _find_unit(target_text)
        parsed = _parse_measurements(source_text)
        if not target or not parsed:
            return None
        measurements, source_display = parsed
    if not measurements:
        return None
    source_unit = UNITS[measurements[0][1]]
    if any(UNITS[unit].dimension != source_unit.dimension for _, unit in measurements):
        return None
    target_unit = UNITS[target]
    if target_unit.dimension != source_unit.dimension:
        return None
    base = 0.0
    for value, unit in measurements:
        if source_unit.dimension == "temperature":
            base += _temperature_to_base(value, unit)
        else:
            base += value * UNITS[unit].factor
    if target_unit.dimension == "temperature":
        converted = _temperature_from_base(base, target)
    else:
        converted = base / target_unit.factor
    result = _number(converted)
    if result is None:
        return None
    exact = math.isclose(float(result), float(converted), rel_tol=1e-9, abs_tol=1e-9)
    verb = "is" if exact else "is about"
    return {"kind": "unit", "expression": f"{source_display} to {target}", "result": result,
            "spoken": f"{source_display} {verb} {_number_text(result)} {target_unit.label}"}


def _currency_key(value: str) -> str | None:
    value = value.lower().strip().rstrip("?.!,")
    return CURRENCY_ALIASES.get(value)


_CURRENCY_PATTERN = "|".join(re.escape(alias) for alias in sorted(CURRENCY_ALIASES, key=len, reverse=True))
_CURRENCY_RE = re.compile(rf"(?<!\w)([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<currency>{_CURRENCY_PATTERN})(?!\w)")


def _load_currency_rates() -> tuple[str, dict[str, float]]:
    path = Path(os.environ.get("HOME", str(Path.home()))) / ".local" / "state" / "milo" / "rates.json"
    try:
        data = json.loads(path.read_text())
        base = str(data["base"]).upper()
        raw_rates = {str(key).upper(): float(value) for key, value in data["rates"].items()}
        raw_rates[base] = 1.0
        if any(not math.isfinite(value) or value <= 0 for value in raw_rates.values()):
            raise ValueError("invalid rate")
        return str(data["as_of"]), raw_rates
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return RATES_AS_OF, dict(STATIC_CURRENCY_RATES)


def _currency_answer(text: str) -> dict[str, Any] | None:
    normalized = _normalise_text(text).rstrip(" ?.!?")
    normalized = re.sub(r"^(?:please\s+)?(?:convert|how much is|what is|whats)\s+", "", normalized)
    split = _split_conversion(normalized)
    if not split:
        return None
    source_text, target_text = split
    target = _currency_key(target_text)
    match = _CURRENCY_RE.fullmatch(source_text)
    if not target or not match:
        return None
    value = float(match.group(1))
    source = _currency_key(match.group("currency"))
    if not source:
        return None
    as_of, rates = _load_currency_rates()
    if source not in rates or target not in rates:
        return None
    result = _number(value / rates[source] * rates[target])
    if result is None:
        return None
    source_label = CURRENCY_NAMES[source][1]
    target_label = CURRENCY_NAMES[target][3]
    return {"kind": "currency", "expression": f"{_number_text(_number(value) or value)} {source} to {target}", "result": result,
            "spoken": f"{_number_text(_number(value) or value)} {source_label} is {_number_text(result)} {target_label} as of {as_of}"}


_SPELL_ASK = re.compile(
    r"^(?:how (?:do|would|should) (?:you|i|we) spell|how is|how's|spell(?: out)?|what is the (?:correct )?spelling of|"
    r"can you spell)\s+(?:the word\s+)?['\"]?([A-Za-z][A-Za-z'-]{1,40})['\"]?(?:\s+spelled)?\s*[?.!]*$", re.IGNORECASE)
_LETTER_COUNT = re.compile(
    r"^how many (?:letter )?([a-z])(?:'s|s)? (?:are |is )?(?:there )?in (?:the word )?['\"]?([A-Za-z][A-Za-z'-]{1,40})['\"]?\s*[?.!]*$",
    re.IGNORECASE)
_PRONOUNS = frozenset("it that this them these those his her him my your".split())
_COUNT_WORDS = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _spelling(text: str) -> dict[str, Any] | None:
    """'How do you spell accommodate?' is answered letter by letter, never by the model's memory."""
    normalized = _ASK_PREFIX.sub("", " ".join(text.split()), count=1).strip()
    normalized = re.sub(r"^(?:hey |ok |okay )?milo[,!]?\s+", "", normalized, flags=re.IGNORECASE)
    if (match := _SPELL_ASK.match(normalized)) and match.group(1).lower() not in _PRONOUNS:
        word = match.group(1)
        letters = ", ".join(letter.upper() for letter in word if letter.isalpha())
        return {"kind": "spelling", "expression": f"spell {word.lower()}", "result": word.lower(),
                "spoken": f"{word[0].upper()}{word[1:].lower()} is spelled {letters}."}
    if (match := _LETTER_COUNT.match(normalized)):
        letter, word = match.group(1).lower(), match.group(2)
        count = word.lower().count(letter)
        many = _COUNT_WORDS.get(count, str(count))
        return {"kind": "spelling", "expression": f"count {letter} in {word.lower()}", "result": count,
                "spoken": f"There {'is' if count == 1 else 'are'} {many} {letter.upper()}{'' if count == 1 else 's'} in {word.lower()}."}
    return None


def answer(text: str) -> dict[str, Any] | None:
    """Return a deterministic calculation result, or None for ordinary speech."""
    if not isinstance(text, str) or not text.strip():
        return None
    for parser in (_spelling, _currency_answer, _unit_answer, _arithmetic):
        result = parser(text)
        if result is not None:
            return result
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    result = answer(" ".join(argv)) if argv else None
    if result is None:
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
