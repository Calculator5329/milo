#!/usr/bin/env python3
"""Extract copyable model text from Milo's spoken reply."""
from __future__ import annotations

import argparse
import json
import re


SHELL_TOOLS = (
    'git', 'sudo', 'pacman', 'systemctl', 'python3', 'pip', 'npm', 'curl',
    'ssh', 'ffmpeg', 'docker', 'ls', 'cd', 'grep', 'find', 'rsync', 'chmod',
    'kill', 'hyprctl', 'journalctl', 'flatpak', 'yay', 'paru', 'nmcli', 'ollama',
    'wl-copy', 'notify-send', 'mkdir', 'cp', 'mv', 'rm', 'cat', 'echo', 'export',
    'bash', 'sh', 'node', 'make', 'python', 'brew', 'apt', 'systemd-run', 'busctl',
    'btrfs', 'mount', 'umount', 'lsblk', 'ln', 'tar', 'ip', 'ps', 'pkill', 'pgrep', 'sed', 'awk',
    'chown', 'df', 'du', 'wget', 'pactl', 'wpctl', 'nvim', 'vim', 'nano', 'code', 'kitty', 'fish',
    'scp', 'sftp', 'tail', 'head', 'less', 'ss', 'lsof', 'htop', 'top', 'free', 'uname', 'ping',
    'dig', 'rg', 'fd', 'which', 'tree', 'xdg-open', 'wtype', 'wl-paste', 'loginctl', 'timedatectl',
    'localectl', 'hostnamectl', 'bluetoothctl', 'nmtui', 'lspci', 'lsusb', 'nvidia-smi', 'dmesg',
    'fc-list', 'xrandr', 'wlr-randr', 'ollama', 'pipx', 'uv', 'cargo', 'rustup', 'go', 'gcc',
)
# A span with a flag, a pipe, a redirect or a path argument is a command even when the tool is
# not in the list above ("ps aux | grep ollama", "ncdu /", "mytool --version").
_COMMAND_SHAPE = re.compile(r"(?:^|\s)-{1,2}[\w-]+|(?:^|\s)[\w.~$@-]*[/~$@:*][\w./~$@:*-]*|\s\|\s|\s(?:>|>>|2>&1)\s?")
_TOOLS = '|'.join(re.escape(tool) for tool in SHELL_TOOLS)
_FENCED = re.compile(r'```(?:[A-Za-z0-9_+.-]+[ \t]*\n)?(.*?)```', re.DOTALL)
_INLINE = re.compile(r'(?<!`)`([^`\n]{3,})`(?!`)')
_URL = re.compile(r'https?://[^\s<>"\'`]+', re.IGNORECASE)
_BARE_DOMAIN = re.compile(
    r'(?<![\w@/.-])(?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*'
    r'\.(?:org|com|net|io|dev|edu|gov|wiki)(?:/[^\s<>"\'`]*)?(?![\w-])',
    re.IGNORECASE,
)
_SPACED_DOMAIN = re.compile(r'\b([a-z0-9-]+)\.\s+(org|com|net|io|dev|edu|gov|wiki)(?=/\S)')
_TRAILING_NOUN = re.compile(
    r'\b(?:the|this|that|a|an)\s+the ((?:(?:second|third|fourth|fifth) )?(?:command|code|link|text)) above me\s+'
    r'(command|flag|option|switch|argument|parameter|path|file|folder|directory|module|package|'
    r'function|method|variable|value|key|keybind|shortcut|script|tool|utility|url|link|address|word|term)\b',
    re.IGNORECASE,
)
_LINK_CUE_TAIL = re.compile(
    r'(?i)\b(?:link|url|site|page|website|wiki|address)\b[^.!?]{0,60}\b[a-z0-9-]+\.'
    r'(?:\s?(?:org|com|net|io|dev|edu|gov|wiki)(?:/\S*)?)?\s?$')
# A word, number, price, short path, or a measurement with its unit: spoken, never bubbled.
_BARE_TOKEN = re.compile(r"[A-Za-z0-9$€£~.][\w.,'%/-]{0,40}|[-\d.,]+\s?°?\s?[A-Za-z%]{1,3}|[-\d.,]+\s?°")
_SPELLED = re.compile(r"(?:[A-Za-z][\s.-]){2,}[A-Za-z]")


def _speak_token(value):
    """'accommodate' stays a word; 'a-c-c-o-m' style spellings become letters read aloud."""
    if _SPELLED.fullmatch(value):
        return ', '.join(ch.upper() for ch in value if ch.isalpha())
    return value


_DOUBLED_PHRASE = re.compile(
    r'\b(?:the|this|that|a)\s+(?:command|code|link|url|text|snippet)\s*[:,]?\s+'
    r'(?=the (?:(?:second|third|fourth|fifth) )?(?:command|code|link|text) above me\b)',
    re.IGNORECASE,
)
_COMMAND_LINE = re.compile(rf'(?m)^[ \t]*(?P<text>(?:{_TOOLS})\b[^\n]*)$')
_ANNOUNCEMENT = re.compile(
    r'(?is)\b(?:the\s+command\s+is|command|run|type|enter|execute|use|try)(?:\s+(?:this|the|it))?'
    r'(?:\s+command)?[ \t]*[:,-]?[ \t]*\n[ \t]*$'
)
_CODE_MARKS = re.compile(r'[=(){}\[\];]|::|->|</?|\\[nrt]|\$\{|\bdef\s|\bclass\s')

WANTS_EXACT_RE = re.compile(
    r"(?ix)"
    r"\bwhat(?:'s|\s+is)\s+the\s+command\b|"
    r"\bgive\s+me\s+(?:the|a)\s+command\b|"
    r"\bhow\s+do\s+i\b[^\n.!?]{0,120}\bin\s+(?:the\s+)?terminal\b|"
    r"\b(?:the|a)\s+(?:url|link)\b|"
    r"\b(?:give|send|show)\s+me\s+(?:the|a)?\s*(?:url|link)\b|"
    r"\bhow\s+(?:do|would)\s+(?:you|i)\s+spell\b|"
    r"\bspell\b(?!\s+it\s+out\b)|"
    r"\bexact\b(?!\s+mean\b)|"
    r"\bcopy\b|\bpaste\b|\bcode\s+for\b|\bone[ -]liner\b|"
    r"\bregex\b|\bsnippet\b|"
    r"(?:\b(?:systemd|git|pacman|python3?)\b[^\n.!?]{0,80}"
    r"\b(?:command|how\s+do\s+i)\b)|"
    r"(?:\b(?:command|how\s+do\s+i)\b[^\n.!?]{0,80}"
    r"\b(?:systemd|git|pacman|python3?)\b)"
)


def wants_exact(question):
    """Return true for copy, paste, command, terminal, URL, link, spelling,
    exact text, code-for, one-liner, regex, snippet, and system tool command asks.
    """
    return bool(WANTS_EXACT_RE.search(question or ''))


def _overlaps(start, end, chosen):
    return any(start < other_end and end > other_start for other_start, other_end, *_ in chosen)


def _inline_kind(value):
    stripped = value.strip()
    if _URL.fullmatch(stripped) or _BARE_DOMAIN.fullmatch(stripped):
        return 'link'
    if re.match(rf'^(?:{_TOOLS})\b', stripped):
        return 'command'
    if '\n' in stripped or _CODE_MARKS.search(stripped):
        return 'code'
    if ' ' in stripped and re.match(r'^[a-z][\w.+-]*\s', stripped) and _COMMAND_SHAPE.search(stripped):
        return 'command'
    return 'text'


def _announced(text, start):
    return bool(_ANNOUNCEMENT.search(text[max(0, start - 180):start]))


_ORDINALS = ('', 'second ', 'third ', 'fourth ', 'fifth ')
_MATH = (
    # A dollar sign that is not a price: $2 = a^2$ has a matching closer, "$5 off" does not.
    (re.compile(r'\$([^$\n]{1,120}?)\$'), lambda m: m.group(0) if re.match(r'\s*\d', m.group(1)) and not re.search(r'[\\^=]', m.group(1)) else m.group(1).strip()),
    (re.compile(r'(?<![\w])\$(?![\d])|(?<=\w)\$(?=[\s.,;:!?)]|$)'), lambda m: ''),
    (re.compile(r'\\sqrt\s*\{([^{}]+)\}|\\sqrt\s*(\w+)'), lambda m: f'the square root of {m.group(1) or m.group(2)}'),
    (re.compile(r'\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}'), lambda m: f'{m.group(1)} over {m.group(2)}'),
    (re.compile(r'(\w|\))\s*\^\s*\{?2\}?(?!\d)'), lambda m: f'{m.group(1)} squared'),
    (re.compile(r'(\w|\))\s*\^\s*\{?3\}?(?!\d)'), lambda m: f'{m.group(1)} cubed'),
    (re.compile(r'(\w|\))\s*\^\s*\{([^{}]+)\}'), lambda m: f'{m.group(1)} to the power {m.group(2)}'),
    (re.compile(r'(\w|\))\s*\^\s*(\w+)'), lambda m: f'{m.group(1)} to the power {m.group(2)}'),
    (re.compile(r'\\(?:cdot|times)'), lambda m: ' times '),
    (re.compile(r'\\(?:neq|ne)\b'), lambda m: ' is not equal to '),
    (re.compile(r'\\(?:le|leq)\b'), lambda m: ' is at most '),
    (re.compile(r'\\(?:ge|geq)\b'), lambda m: ' is at least '),
    (re.compile(r'\\(?:in|mathbb|text|mathrm|left|right|,|;|!)\b'), lambda m: ''),
)


def speakable(spoken):
    """Verbalise math markup a cloud model may emit: $\\sqrt{2}$ and a^2/b^2 are not sayable."""
    if '\\' not in spoken and '^' not in spoken and '$' not in spoken:
        return spoken
    for pattern, replacement in _MATH:
        spoken = pattern.sub(replacement, spoken)
    spoken = spoken.replace('{', '').replace('}', '')
    return re.sub(r'[ \t]{2,}', ' ', spoken)


def extract(text, seen=None):
    """Return spoken text and ordered copyable thoughts found in model text. seen counts the
    thoughts already shown this turn per kind, so a second command is spoken as "the second
    command above me" instead of an indistinguishable repeat."""
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    text = _SPACED_DOMAIN.sub(r'\1.\2', text)

    chosen = []
    for match in _FENCED.finditer(text):
        value = match.group(1).strip()
        if value:
            chosen.append((match.start(), match.end(), value, 'code'))

    inline_words = []
    for match in _INLINE.finditer(text):
        if not _overlaps(match.start(), match.end(), chosen):
            value = match.group(1).strip()
            if len(value) >= 3:
                kind = _inline_kind(value)
                if kind in ('text', 'command') and _BARE_TOKEN.fullmatch(value):
                    # A plain word, number or name in backticks ("`accommodate`", "`206`",
                    # "`local-ai-lab`") is spoken as itself; a bubble would only hide it.
                    inline_words.append((match.start(), match.end(), value))
                    continue
                chosen.append((match.start(), match.end(), value, kind))

    for match in _URL.finditer(text):
        end = match.end()
        value = match.group(0).rstrip('.,!?;:)]}')
        end -= len(match.group(0)) - len(value)
        if value and not _overlaps(match.start(), end, chosen):
            chosen.append((match.start(), end, value, 'link'))

    for match in _BARE_DOMAIN.finditer(text):
        end = match.end()
        value = match.group(0).rstrip('.,!?;:)]}')
        end -= len(match.group(0)) - len(value)
        if value and not _overlaps(match.start(), end, chosen):
            chosen.append((match.start(), end, value, 'link'))

    for match in _COMMAND_LINE.finditer(text):
        start, end = match.span('text')
        value = match.group('text').rstrip()
        end = start + len(value)
        if _announced(text, match.start()) and not _overlaps(start, end, chosen):
            chosen.append((start, end, value, 'command'))

    if inline_words:
        # Unwrap the backticks in place so later spans keep their offsets relative to the
        # rewritten text; both lists are rebuilt from the same character positions.
        pieces, cursor, shift, adjusted = [], 0, 0, []
        removed = sorted(inline_words, key=lambda item: item[0])
        for start, end, value in removed:
            pieces.append(text[cursor:start])
            pieces.append(_speak_token(value))
            cursor = end
        pieces.append(text[cursor:])
        rebuilt = ''.join(pieces)
        for start, end, value, kind in chosen:
            delta = sum(len(text[s:e]) - len(_speak_token(v)) for s, e, v in removed if e <= start)
            adjusted.append((start - delta, end - delta, value, kind))
        text, chosen = rebuilt, adjusted
    if not chosen:
        return text, []

    chosen.sort(key=lambda item: item[0])
    phrases = {
        'command': 'the command above me',
        'code': 'the code above me',
        'link': 'the link above me',
        'text': 'the text above me',
    }
    parts = []
    cursor = 0
    counts = dict(seen or {})
    for start, end, _value, kind in chosen:
        ordinal = _ORDINALS[min(counts.get(kind, 0), len(_ORDINALS) - 1)]
        parts.extend((text[cursor:start], phrases[kind].replace('the ', 'the ' + ordinal, 1)))
        counts[kind] = counts.get(kind, 0) + 1
        cursor = end
    parts.append(text[cursor:])
    spoken = ''.join(parts).strip()
    spoken = re.sub(r'[ \t]{2,}', ' ', spoken)
    spoken = re.sub(r'[ \t]*\n[ \t]*', ' ', spoken)
    spoken = re.sub(r'\s+([,!?;:]|\.(?!\w))', r'\1', spoken)
    spoken = tidy(spoken)
    thoughts = [{'text': value, 'kind': kind} for _start, _end, value, kind in chosen]
    return spoken, thoughts


def _ordinal(phrase):
    """'second command' -> 'second ', 'command' -> ''."""
    words = phrase.split()
    return words[0] + ' ' if len(words) > 1 else ''


def tidy(spoken, terminal=False):
    """Collapse the model's own carrier words around a placeholder. Runs on every finished
    sentence as well as inside extract(), because a streamed span can close before its trailing
    noun arrives ("Use the `pacman -Ss`" ... " command followed by"). terminal marks a finished
    sentence, which may end on a placeholder with no full stop once the stream closes."""
    spoken = speakable(spoken)
    if 'above me' not in spoken:
        return spoken
    spoken = _DOUBLED_PHRASE.sub('', spoken)
    # "Here is the link to the article: the link above me" is a stitched fragment.
    spoken = re.sub(r"\b(here(?:'s| is)|there(?:'s| is)|this is|that is|use|try|run)\s+(?:the|a|an)\s+(?:link|command|code|url|snippet)(?:\s+(?:to|for|of)\s+[^:.!?]{0,80})?:\s*(?=the (?:\w+ )?(?:command|code|link|text) above me\b)", r"\1 ", spoken, flags=re.IGNORECASE)
    # "the `hyprctl reload` command" and "the `--user` flag" arrive as "the the command above me
    # command" and "the the text above me flag": keep one article and the better noun.
    spoken = _TRAILING_NOUN.sub(lambda m: f'the {_ordinal(m.group(1))}{m.group(2)} above me', spoken)
    spoken = re.sub(r'\b(?:the|this|that|a|an)\s+(the (?:(?:second|third|fourth|fifth) )?(?:command|code|link|text) above me)\b', r'\1', spoken, flags=re.IGNORECASE)
    # "use `btrfs subvolume snapshot` command" (no article) arrives as "use the text above me command".
    spoken = re.sub(r'\bthe ((?:(?:second|third|fourth|fifth) )?)(?:text|code|command) above me (command|flag|option|script|snippet|file|module|package)\b', r'the \1\2 above me', spoken, flags=re.IGNORECASE)
    spoken = re.sub(r'(?:^|(?<=[.!?] ))the (?=(?:(?:second|third|fourth|fifth) )?(?:command|code|link|text|flag|option|script|snippet|file|module|package) above me\b)', 'The ', spoken)
    if terminal and spoken.endswith('above me'):
        spoken += '.'
    return spoken


def pending(text):
    """True while a streaming URL, backtick span, link, or command line is still open.

    Callers hold the whole buffer back from the sentence splitter while this is
    true; otherwise a link is cut at its first dot and read aloud in pieces.
    """
    without_fences = _FENCED.sub('', text)
    if text.count('```') % 2 or without_fences.count('`') % 2:
        return True
    if re.search(r'https?://[^\s<>"\'`]*$', text, re.IGNORECASE):
        return True
    if re.search(r'[a-z0-9.-]+\.[a-z]{2,4}(?:/[^\s<>"\'`]*)?$', text, re.IGNORECASE):
        return True
    if _LINK_CUE_TAIL.search(text):
        return True
    return any(match.end() == len(text) and _announced(text, match.start())
               for match in _COMMAND_LINE.finditer(text))


def extract_stream(text, final=False, seen=None):
    """Extract only when a streaming URL, backtick span, or command line is complete."""
    if not final and pending(text):
        return text, []
    return extract(text, seen=seen)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('text', help='model reply to inspect')
    args = parser.parse_args(argv)
    print(json.dumps(extract(args.text), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
