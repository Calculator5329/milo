"""Repair words the speech recogniser reliably mishears before Milo routes the turn.

Whisper turns technical names into the nearest everyday words: "Hyprland" arrives as
"Hyperland", "pacman" as "Pac-Man", "systemd" as "systemmd" or "system empty", "btrfs" as
"bit rifts". The ledger for 2026-09-13 shows every one of these reaching the router and the
model, which then either shrugged or looked up cartoons. Each rule here is a high confidence
substitution; nothing ambiguous belongs in the table (a plain "lama" stays a lama).
"""
import re

RULES = (
    (r"\bhyper\s*land\b|\bhyprlan\b|\bhyper\s*lund\b", 'Hyprland'),
    (r"\bpac[\s-]?man\b", 'pacman'),
    (r"\bsystem\s*m+d\b|\bsystem[\s-]?d\b|\bsystem\s+empty\b|\bsystem\s+md\b", 'systemd'),
    (r"\bsystemd\s+timers?\b", 'systemd timers'),
    (r"\bneo\s+vim\b|\bneovim\b", 'Neovim'),
    (r"\bbit\s+rifts?\b|\bbutter\s*(?:fs|f\s*s|efs)\b|\bb\s*tr\s*fs\b", 'btrfs'),
    (r"\bfair\s+and\s+height\b|\bfaren\s*height\b|\bfahren\s+heit\b", 'Fahrenheit'),
    (r"\bff\s*mpeg\b|\bf\s*f\s*m\s*peg\b|\beff\s*em\s*peg\b", 'ffmpeg'),
    (r"\bcachy\s*os\b|\bcatchy\s*os\b|\bcashy\s*os\b|\bcache\s*e\s*os\b", 'CachyOS'),
    (r"\bo\s*llama\b|\boh\s+llama\b|\balama\b", 'Ollama'),
    (r"\bgit\s+hub\b", 'GitHub'),
    (r"\bway\s+land\b", 'Wayland'),
    (r"\bkitty\s+terminal\b", 'kitty terminal'),
    (r"\bnew\s+vim\b", 'Neovim'),
    (r"\bpie\s+thon\b", 'Python'),
    (r"\bwhisper\s+dot\s+cpp\b|\bwhisper\s+c\s*p\s*p\b", 'whisper.cpp'),
    (r"\barch\s+wiki\b", 'Arch wiki'),
    (r"\bsis\s+admin\b", 'sysadmin'),
    (r"\bs\s+s\s+h\b", 'ssh'),
    (r"\bjay\s+son\b", 'JSON'),
    (r"\bkubernetes\b|\bkuber\s*netties\b", 'Kubernetes'),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in RULES)


def correct(text):
    """Return the transcript with known mishearings repaired; unchanged text stays identical."""
    if not text:
        return text
    fixed = text
    for pattern, replacement in _COMPILED:
        fixed = pattern.sub(replacement, fixed)
    return fixed


def corrections(text):
    """The (heard, repaired) pairs that correct() would apply, for receipts and tests."""
    found = []
    for pattern, replacement in _COMPILED:
        for match in pattern.finditer(text or ''):
            if match.group(0) != replacement:
                found.append((match.group(0), replacement))
    return found
