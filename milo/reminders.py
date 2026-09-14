#!/usr/bin/env python3
"""Local systemd-backed timers and reminders for Milo."""
from __future__ import annotations

import argparse
import builtins
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys


DEFAULT_STORE = Path.home() / '.local' / 'state' / 'milo' / 'reminders.json'
DEFAULT_LOG = Path.home() / '.local' / 'state' / 'milo' / 'reminders.log'
SCRIPT = Path(__file__).resolve()
ID_PATTERN = re.compile(r'[0-9a-f]{6}')

SMALL = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
    'eighteen': 18, 'nineteen': 19,
}
TENS = {
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}
UNIT_SECONDS = {'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400}


def store_path():
    return Path(os.environ.get('MILO_REMINDERS', str(DEFAULT_STORE))).expanduser()


def log_path():
    return Path(os.environ.get('MILO_REMINDERS_LOG', str(DEFAULT_LOG))).expanduser()


def load():
    """Load the retained entries, treating a missing store as empty."""
    path = store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError('The reminder store is unavailable.') from error
    if not isinstance(data, builtins.list) or any(not isinstance(row, dict) for row in data):
        raise RuntimeError('The reminder store is invalid.')
    return data


def save(entries):
    """Atomically replace the retained reminder entries."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError('The reminder store could not be updated.') from error


def _now(value=None):
    return value or datetime.datetime.now().astimezone()


def _clean_label(value):
    value = re.sub(r'\s+', ' ', value).strip(' \t\n.,!?')
    return value


def _number(value):
    value = value.lower().replace('-', ' ').strip()
    if value in ('a', 'an'):
        return 1
    if value.isdigit():
        number = int(value)
        return number if 0 < number <= 999 else None
    tokens = [token for token in value.split() if token != 'and']
    if not tokens:
        return None
    total = 0
    current = 0
    used = False
    for token in tokens:
        if token in SMALL:
            current += SMALL[token]
        elif token in TENS:
            current += TENS[token]
        elif token == 'hundred' and 0 < current < 10:
            current *= 100
        else:
            return None
        used = True
    total += current
    return total if used and 0 < total <= 999 else None


def _duration(value):
    match = re.fullmatch(r'(.+?)\s+(seconds?|minutes?|hours?|days?)', value.strip(), re.IGNORECASE)
    if not match:
        return None
    amount = _number(match.group(1))
    unit = match.group(2).lower().rstrip('s')
    if amount is None:
        return None
    return amount * UNIT_SECONDS[unit]


def _relative(kind, duration_text, label, now):
    delay = _duration(duration_text)
    label = _clean_label(label)
    if delay is None or not label:
        return None
    return {
        'kind': kind,
        'when': (now + datetime.timedelta(seconds=delay)).isoformat(timespec='seconds'),
        'delay_seconds': delay,
        'label': label,
    }


def _clock(value, now, tomorrow=False):
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', value.strip(), re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or '').lower()
    if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
        return None
    if meridiem:
        hour = hour % 12 + (12 if meridiem == 'pm' else 0)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow or target <= now:
        target += datetime.timedelta(days=1)
    return target


def parse(text, now=None):
    """Recognize a bounded reminder grammar, returning None for normal questions."""
    if not isinstance(text, str):
        return None
    raw = re.sub(r'\s+', ' ', text).strip()
    normalized = raw.lower().strip(' \t\n.,!?')
    command = raw.strip(' \t\n.,!?')
    if normalized in ('what reminders do i have', 'list my reminders', 'list reminders',
                      'what timers do i have', 'list my timers'):
        return {'kind': 'list'}

    match = re.fullmatch(r'cancel all (?:reminders|timers)', normalized)
    if match:
        return {'kind': 'cancel', 'label': 'all'}
    if normalized in ('cancel the timer', 'cancel timer'):
        return {'kind': 'cancel', 'label': 'timer'}
    match = re.fullmatch(r'cancel (?:the )?(.+?) (?:reminder|timer)', command, re.IGNORECASE)
    if match:
        label = _clean_label(match.group(1))
        return {'kind': 'cancel', 'label': label} if label else None

    current = _now(now)
    match = re.fullmatch(r'set (?:a )?timer for (.+)', command, re.IGNORECASE)
    if match:
        return _relative('timer', match.group(1), 'timer', current)

    patterns = (
        r'remind me in (?P<duration>.+?) to (?P<label>.+)',
        r'remind me to (?P<label>.+?) in (?P<duration>.+)',
        r'in (?P<duration>.+?) remind me to (?P<label>.+)',
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, command, re.IGNORECASE)
        if match:
            result = _relative('create', match.group('duration'), match.group('label'), current)
            if result:
                return result

    clock = r'(?P<clock>\d{1,2}(?::\d{2})?\s*(?:am|pm|o\'?clock)?)'
    absolute_patterns = (
        r'remind me at ' + clock + r'(?: (?P<tomorrow>tomorrow))? to (?P<label>.+)',
        r'at ' + clock + r'(?: (?P<tomorrow>tomorrow))? remind me to (?P<label>.+)',
        r'tomorrow at ' + clock + r' remind me to (?P<label>.+)',
        r'remind me to (?P<label>.+?) tomorrow at ' + clock,
        r'remind me (?P<tomorrow>tomorrow )?to (?P<label>.+?) (?:at|by) ' + clock + r'(?: (?P<tomorrow2>tomorrow))?',
        r'wake me (?:up )?(?:tomorrow )?at ' + clock + r'(?: (?P<tomorrow>tomorrow))?',
        r'set (?:a |an )?(?:reminder|alarm) (?:for |at )' + clock + r'(?: (?P<tomorrow>tomorrow))? (?:to|for) (?P<label>.+)',
    )
    for pattern in absolute_patterns:
        match = re.fullmatch(pattern, command, re.IGNORECASE)
        if not match:
            continue
        groups = match.groupdict()
        label = _clean_label(groups.get('label') or 'wake up')
        tomorrow = bool(groups.get('tomorrow') or groups.get('tomorrow2')) or pattern.startswith('tomorrow') \
            or bool(re.search(r'\btomorrow\b', command, re.IGNORECASE))
        target = _clock(re.sub(r"\s*o'?clock$", '', groups['clock'], flags=re.IGNORECASE), current, tomorrow)
        if label and target:
            return {'kind': 'create', 'when': target.isoformat(timespec='seconds'),
                    'delay_seconds': None, 'label': label}

    day_patterns = (
        r'remind me (?:on |this |next )?(?P<day>' + _DAY_WORDS + r') to (?P<label>.+)',
        r'remind me to (?P<label>.+?) (?:on |this |next )?(?P<day>' + _DAY_WORDS + r')',
        r'set (?:a |an )?reminder (?:for |on )(?:this |next )?(?P<day>' + _DAY_WORDS + r') (?:to|for) (?P<label>.+)',
        r'(?:on |this |next )?(?P<day>' + _DAY_WORDS + r') remind me to (?P<label>.+)',
    )
    for pattern in day_patterns:
        match = re.fullmatch(pattern, command, re.IGNORECASE)
        if not match:
            continue
        label = _clean_label(match.group('label'))
        target = _day(match.group('day'), current)
        if label and target:
            return {'kind': 'create', 'when': target.isoformat(timespec='seconds'),
                    'delay_seconds': None, 'label': label}
    return None


_DAY_WORDS = (r'tomorrow morning|tomorrow afternoon|tomorrow evening|tomorrow night|tonight|tomorrow|'
              r'monday|tuesday|wednesday|thursday|friday|saturday|sunday')
_DAY_HOURS = {'morning': 9, 'afternoon': 14, 'evening': 18, 'night': 21, 'tonight': 21, 'tomorrow': 9}
_WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def _day(word, now):
    """'tomorrow morning' -> tomorrow 09:00; 'Friday' -> the next Friday at 09:00; 'tonight' -> 21:00."""
    word = ' '.join(word.lower().split())
    if word == 'tonight':
        target = now.replace(hour=21, minute=0, second=0, microsecond=0)
        return target if target > now else target + datetime.timedelta(days=1)
    if word.startswith('tomorrow'):
        part = word.split()[-1]
        hour = _DAY_HOURS.get(part, 9)
        return (now + datetime.timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if word in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(word) - now.weekday()) % 7 or 7
        return (now + datetime.timedelta(days=ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _completed(result, failure):
    if result.returncode:
        detail = (result.stderr or result.stdout or failure).strip()
        raise RuntimeError(detail or failure)
    return result


def create(entry, runner=subprocess.run):
    """Create one transient systemd timer and retain its receipt."""
    if not isinstance(entry, dict) or entry.get('kind') not in ('create', 'timer'):
        raise ValueError('Invalid reminder entry.')
    label = _clean_label(str(entry.get('label', '')))
    if not label or not entry.get('when'):
        raise ValueError('Invalid reminder entry.')
    existing = load()
    identifier = None
    for _attempt in range(12):
        candidate = secrets.token_hex(3)
        if not any(row.get('id') == candidate for row in existing):
            identifier = candidate
            break
    if identifier is None:
        raise RuntimeError('Could not allocate a reminder receipt.')
    unit_base = 'milo-reminder-' + identifier
    if entry.get('delay_seconds') is not None:
        schedule = '--on-active=' + str(int(entry['delay_seconds']))
    else:
        try:
            calendar_when = datetime.datetime.fromisoformat(str(entry['when'])).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError as error:
            raise ValueError('Invalid reminder time.') from error
        schedule = '--on-calendar=' + calendar_when
    argv = [
        'systemd-run', '--user', schedule, '--unit=' + unit_base,
        '--description=Milo reminder: ' + label, '--collect', '/usr/bin/python3',
        str(SCRIPT), '--fire', identifier,
    ]
    try:
        result = runner(argv, capture_output=True, text=True)
    except OSError as error:
        raise RuntimeError('The user timer manager is unavailable.') from error
    _completed(result, 'The user timer manager rejected the reminder.')
    stored = {
        'id': identifier,
        'label': label,
        'when': str(entry['when']),
        'unit': unit_base + '.timer',
        'created': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
    }
    try:
        save(existing + [stored])
    except Exception:
        try:
            runner(['systemctl', '--user', 'stop', stored['unit']], capture_output=True, text=True)
        except OSError:
            pass
        raise
    return stored


def list(runner=subprocess.run):
    """Return retained timers that still exist in the user manager."""
    entries = load()
    argv = ['systemctl', '--user', 'list-timers', '--all', '--no-legend', 'milo-reminder-*']
    try:
        result = runner(argv, capture_output=True, text=True)
    except OSError as error:
        raise RuntimeError('The user timer manager is unavailable.') from error
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'The user timer manager is unavailable.').strip())
    active = set(re.findall(r'\bmilo-reminder-[0-9a-f]{6}\.timer\b', result.stdout or ''))
    kept = [entry for entry in entries if entry.get('unit') in active]
    if kept != entries:
        save(kept)
    return kept


def cancel(identifier, runner=subprocess.run):
    """Stop one timer by its short id and remove its retained entry."""
    if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
        raise ValueError('Invalid reminder id.')
    entries = load()
    entry = next((row for row in entries if row.get('id') == identifier), None)
    if entry is None:
        raise ValueError('That reminder is no longer active.')
    argv = ['systemctl', '--user', 'stop', 'milo-reminder-' + identifier + '.timer']
    try:
        result = runner(argv, capture_output=True, text=True)
    except OSError as error:
        raise RuntimeError('The user timer manager is unavailable.') from error
    _completed(result, 'The reminder could not be cancelled.')
    save([row for row in entries if row.get('id') != identifier])
    return entry


def _log_fire(entry, delivery):
    path = log_path()
    row = {
        'at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'id': entry['id'], 'label': entry['label'], 'delivery': delivery,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    except OSError:
        pass


def fire(identifier, runner=subprocess.run):
    """Deliver a fired reminder to Milo, falling back to a desktop notice."""
    if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
        raise ValueError('Invalid reminder id.')
    entries = load()
    entry = next((row for row in entries if row.get('id') == identifier), None)
    if entry is None:
        raise ValueError('That reminder is no longer active.')
    spoken = 'Reminder: ' + entry['label'].rstrip('.!?') + '.'
    gdbus = [
        'gdbus', 'call', '--session', '--dest', 'org.milo.Companion',
        '--object-path', '/org/milo/Companion', '--method',
        'org.milo.Companion.Say', spoken,
    ]
    try:
        result = runner(gdbus, capture_output=True, text=True)
    except OSError:
        result = subprocess.CompletedProcess(gdbus, 1, '', 'gdbus unavailable')
    delivery = 'gdbus'
    if result.returncode:
        notify = ['notify-send', 'Milo reminder', spoken]
        try:
            notice = runner(notify, capture_output=True, text=True)
            delivery = 'notify-send' if notice.returncode == 0 else 'failed'
        except OSError:
            delivery = 'failed'
    save([row for row in entries if row.get('id') != identifier])
    _log_fire(entry, delivery)
    return {**entry, 'delivery': delivery}


def _words(number):
    reverse_small = {value: key for key, value in SMALL.items()}
    reverse_tens = {value: key for key, value in TENS.items()}
    if number < 20:
        return reverse_small[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return reverse_tens[tens * 10] + ((' ' + reverse_small[rest]) if rest else '')
    hundreds, rest = divmod(number, 100)
    return reverse_small[hundreds] + ' hundred' + ((' ' + _words(rest)) if rest else '')


def _duration_words(seconds):
    for unit, size in (('day', 86400), ('hour', 3600), ('minute', 60), ('second', 1)):
        if seconds % size == 0:
            amount = seconds // size
            return _words(amount) + ' ' + unit + ('' if amount == 1 else 's')
    return _words(seconds) + ' seconds'


def _spoken_time(value):
    when = datetime.datetime.fromisoformat(value)
    hour = when.strftime('%I').lstrip('0') or '12'
    minute = when.strftime(':%M') if when.minute else ''
    return hour + minute + ' ' + when.strftime('%p')


def _match_label(entries, label):
    wanted = label.casefold()
    exact = [entry for entry in entries if str(entry.get('label', '')).casefold() == wanted]
    if exact:
        return exact[0]
    partial = [entry for entry in entries if wanted in str(entry.get('label', '')).casefold()]
    return partial[0] if len(partial) == 1 else None


def handle(text, now=None):
    """Parse and execute one reminder utterance, with a spoken result and receipt."""
    intent = parse(text, now)
    if intent is None:
        return None
    kind = intent['kind']
    try:
        if kind in ('create', 'timer'):
            entry = create(intent)
            if intent['delay_seconds'] is not None:
                duration = _duration_words(intent['delay_seconds'])
                spoken = ('Okay, timer set for ' + duration + '.' if kind == 'timer'
                          else 'Okay, in ' + duration + ': ' + intent['label'].rstrip('.!?') + '.')
            else:
                spoken = 'Okay, at ' + _spoken_time(intent['when']) + ': ' + intent['label'].rstrip('.!?') + '.'
            return {'spoken': spoken, 'receipt': entry['unit'], 'action': 'create'}
        if kind == 'list':
            entries = list()
            receipts = [entry['unit'] for entry in entries]
            if not entries:
                spoken = 'You have no active reminders.'
            elif len(entries) == 1:
                entry = entries[0]
                spoken = 'You have one reminder: ' + entry['label'] + ', at ' + _spoken_time(entry['when']) + '.'
            else:
                labels = ', '.join(entry['label'] for entry in entries[:3])
                extra = ' and ' + str(len(entries) - 3) + ' more' if len(entries) > 3 else ''
                spoken = 'You have ' + str(len(entries)) + ' reminders: ' + labels + extra + '.'
            return {'spoken': spoken, 'receipt': receipts, 'action': 'list'}
        entries = list()
        if intent['label'] == 'all':
            removed = [cancel(entry['id']) for entry in entries]
            return {
                'spoken': ('Cancelled ' + str(len(removed)) + ' reminders.' if removed
                           else 'You have no active reminders to cancel.'),
                'receipt': [entry['unit'] for entry in removed], 'action': 'cancel',
            }
        entry = _match_label(entries, intent['label'])
        if entry is None:
            return {'spoken': 'I could not find that reminder.', 'action': 'cancel',
                    'error': 'No unique active reminder matched.'}
        removed = cancel(entry['id'])
        return {'spoken': 'Cancelled the ' + removed['label'] + ' reminder.',
                'receipt': removed['unit'], 'action': 'cancel'}
    except (OSError, RuntimeError, ValueError) as error:
        subject = 'timer' if kind == 'timer' else 'reminder'
        verb = 'cancel' if kind == 'cancel' else ('list' if kind == 'list' else 'set')
        return {'spoken': 'I could not ' + verb + ' that ' + subject + '.',
                'action': 'cancel' if kind == 'cancel' else ('list' if kind == 'list' else 'create'),
                'error': str(error)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--list', action='store_true', dest='show')
    group.add_argument('--cancel', metavar='ID')
    group.add_argument('--fire', metavar='ID')
    parser.add_argument('text', nargs='?')
    args = parser.parse_args(argv)
    try:
        if args.show:
            result = list()
        elif args.cancel:
            result = cancel(args.cancel)
        elif args.fire:
            result = fire(args.fire)
        elif args.text:
            result = handle(args.text)
            if result is None:
                parser.error('text is not a timer or reminder command')
        else:
            parser.error('supply reminder text, --list, --cancel, or --fire')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not isinstance(result, dict) or 'error' not in result else 1
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
