"""Pre-answer router (M1): decide whether a turn reads the offline library before the model speaks.

Plain questions used to bypass the 29 books unless the user said 'look up'. The router
scores the shape of the utterance (question words, named things, dated facts) against
personal and advisory cues, and in the middle band asks Kiwix for title suggestions,
a probe that costs a few milliseconds per book. Every decision is a receipt the turn
ledger keeps, so false lookups and missed ones are visible in ``turn_ledger.py --flag``.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

LIBRARY_DIR = str(pathlib.Path(__file__).resolve().parent)  # the vendored library package sits beside this file
if LIBRARY_DIR not in sys.path:
    sys.path.insert(0, LIBRARY_DIR)

import calc_tool  # noqa: E402
from local_search import definition_term  # noqa: E402
from library.personal_book import strip_trigger as personal_trigger  # noqa: E402
from web_search import requested_query, requested_web  # noqa: E402

PROBE_BUDGET_S = 0.060
PROBE_WORKERS = 2  # kiwix-serve runs four threads; abandoned probes must never starve the real search
PROBE_COOLDOWN_S = 30.0  # a book that missed the budget (cold pages on the slow drive) is skipped for a while
PROBE_BOOKS_RE = re.compile(r'href="/content/((?:wikipedia|wiktionary|wikibooks|wikivoyage|archlinux|unix\.stackexchange)[^"/]*)')
KIWIX_URL = os.environ.get('MILO_KIWIX_URL', '').rstrip('/')
MAX_QUERY = 200

_WORDS = lambda pattern: re.compile(r'\b(?:' + pattern + r')\b', re.I)  # noqa: E731

SMALL_TALK = re.compile(r"^(?:hi|hello|hey|yo|thanks?|thank you|cheers|ok(?:ay)?|yes|yeah|yep|no|nope|sure|good (?:morning|afternoon|evening|night)|bye|goodbye|see you|never ?mind|sorry|cool|nice|great|wow|hmm+|uh+|um+)[\s.,!?]*(?:milo)?[\s.,!?]*$", re.I)
OPINION = _WORDS(r"do you (?:think|like|feel|prefer|believe|reckon)|what do you think|would you|should i|should we|can you help me decide|recommend|advice|advise|your (?:opinion|take|favou?rite)|opinion|thoughts on|feel about|honestly|(?:good|best|better|nice|smart|right) way to|ways to|tips|ideas for|worth (?:it|learning|trying|doing|buying)|is it worth|who(?:'s| is) (?:better|best|the best|the greatest|the goat)|(?:is|are) .{1,40} (?:better|worse) than|better to|overrated|underrated|greatest of all time|the goat")
PERSONAL_NOTE_ASK = _WORDS(r"my notes|check my notes|in my notes|my life")
# Questions about Milo itself never earn a lookup; the system prompt carries the capability card.
SELF = _WORDS(r"(?:what|which) (?:model|llm|ai|language model|voice|speech recogni[sz]er) (?:are you|do you (?:use|run)|powers you|is this)|are you (?:running|built|based|trained) on|what are you (?:running|built|based) on|do you (?:remember|have (?:a )?memory|keep|store|save|record|listen|hear|see)|can you (?:remember|hear|listen|see|run|execute|access|read|search|browse|remind|set)|are you (?:always )?(?:listening|recording|online|offline|local|connected|always on|private)|(?:is|are) (?:my|our) (?:data|conversations?|questions?|voice|audio) (?:private|stored|saved|sent|recorded|uploaded)|does (?:anything|any of this|my data|my voice) (?:leave|go to)|where (?:do|does) (?:my|the) (?:data|voice|audio|questions?) go|how do you (?:work|listen|hear|know)|what can you do|what (?:do|can) you (?:know|remember) about me")
PERSONAL = _WORDS(r"i|i'm|i've|i'd|i'll|me|my|mine|myself|we|we're|our|us|you and i")
CONVERSATIONAL = _WORDS(r"tell me a (?:joke|story|riddle)|joke|story|poem|sing|pretend|roleplay|role play|let's (?:play|talk|chat)|how are you|who are you|what are you|what's your name|your name|good job|well done|thank")
COMMAND = re.compile(r"^(?:please\s+)?(?:remind me|set (?:a |an )?(?:timer|alarm|reminder)|timer for|wake me|open|close|play|pause|stop|resume|mute|unmute|launch|start|switch|focus|move|go to|show me|screenshot|type|write|draft|compose|email|message|text|call|translate|summari[sz]e|rewrite|rephrase|shorten|expand|proofread|fix|convert|calculate|compute|what time is it|what day is it|what's the (?:time|date))\b", re.I)
MATH = re.compile(r"\d\s*(?:[-+*/x×÷^%]|plus|minus|times|divided by|percent of|percent off|to the power)\s*\d|square root|\bsqrt\b|how much is \d|what is \d[\d.,]*\s*(?:[-+*/x×÷^%]|plus|minus|times|divided)", re.I)
QUESTION_START = re.compile(r"^(?:(?:hey |ok |okay )?milo[,!]?\s+)?(?:so\s+|um+\s+|uh+\s+)?(?:(?:can|could|would) you (?:please )?(?:tell me|explain|remind me)?\s*)?(?:please\s+)?(who|whom|whose|what|what's|whats|when|when's|where|where's|why|why's|which|how|how's)\b", re.I)
FACTUAL_LEAD = re.compile(r"^(?:(?:hey |ok |okay )?milo[,!]?\s+)?(?:(?:can|could|would) you (?:please )?)?(?:please\s+)?(?:tell me about|explain|describe|define|give me (?:a |an )?(?:summary|overview|rundown|explanation) of|what do you know about|teach me about|history of|the history of|summari[sz]e)\b", re.I)
FACT_VERBS = _WORDS(r"invented|discovered|founded|wrote|written|composed|painted|directed|built|born|died|happened|located|capital|population|height|tallest|longest|largest|smallest|oldest|first|difference between|compared to|versus|vs|means|meaning|definition|origin|etymology|history|cause|causes|caused|symptoms|treatment|how (?:does|do|did) .{1,40}? work|made of|consist|distance|boiling|freezing|speed of|formula|theorem|law of|element|planet|species|country|city|river|mountain|war|battle|empire|dynasty|century|language")
HOW_TO = re.compile(r"^(?:(?:hey |ok |okay )?milo[,!]?\s+)?(?:(?:can|could|would) you (?:please )?(?:tell me |show me |explain )?)?(?:please\s+)?how (?:do|does|can|could|would|should|to|did) (?:i|you|we|one|someone|people|it)?\s*(.+)", re.I)
TECH = _WORDS(r"linux|arch|cachy ?os|gentoo|ubuntu|debian|fedora|windows|mac ?os|bash|shell|terminal|command|commands|cli|git|github|branch|commit|merge|rebase|docker|kubernetes|nginx|postgres(?:ql)?|sqlite|mysql|python|javascript|typescript|node|react|rust|go(?:lang)?|c\+\+|cpp|java|html|css|dom|api|http|json|regex|systemd|btrfs|ext4|zfs|filesystem|kernel|driver|gpu|cuda|nvidia|ssh|dns|tcp|ip|vpn|wifi|bluetooth|package|pacman|apt|pip|npm|cargo|compile|compiler|library|function|class|variable|array|string|thread|process|memory|disk|partition|mount|permission|chmod|grep|find|sed|awk|vim|neovim|emacs|tmux|hyprland|wayland|x11|pipewire|alsa|ffmpeg|whisper|ollama|model|llm|neural|transformer|machine learning|dataset|training|inference|algorithm|data structure|sql|query|database|server|client|socket|port|firewall")
YEAR = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})s?\b")
PROPER = re.compile(r"(?<=[a-z,;]\s)(?!(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December|Milo|Okay)\b)(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)")
STOP_TOPIC = re.compile(r"^(?:(?:hey |ok |okay )?milo[,!]?\s+)?(?:so\s+|um+\s+|uh+\s+)?(?:(?:can|could|would) you (?:please )?)?(?:please\s+)?(?:tell me (?:about )?|explain (?:to me )?|describe |define |what do you know about |teach me about |remind me )?(?:(?:who|whom|whose|what|what's|whats|when|when's|where|where's|why|why's|which|how|how's)\s+)?(?:(?:is|are|was|were|does|do|did|has|have|had|can|could|would|should|will|many|much|long|far|old|tall|big|often|come)\s+)?(?:(?:i|you|we|one|someone)\s+)?(?:(?:the|a|an|some|any|about|of|it|that|this|there)\s+)*", re.I)
# News asks read the freshness index (M2). Facts that can change within a day use live web search.
NEWS = _WORDS(r"news|headlines|current events|what happened(?:\s+\w+){0,4}\s+this week|what's (?:happening|going on|new)|announced|released")
LIVE = _WORDS(r"stock|stocks|share price|market|markets|index level|price of|exchange rate|weather|forecast|temperature|scores?|game results?|who won|is .{1,80} open (?:right )?now|current time in|trading at|s ?& ?p(?: 500)?|s and p 500|nasdaq|dow(?: jones)?|bitcoin|ethereum|crypto|dogecoin|nvidia|tesla|apple stock")
RECENT = _WORDS(r"latest|recent|recently|newest|today's|this (?:week|morning)|currently|right now|lately|these days|nowadays")
# Workspace asks read the private book of repo docs (M9); the trigger names the workspace or a repo status.
RECAP = _WORDS(r"what (?:have|did|had) we (?:talked|spoken|chatted|discussed|talk|speak|chat) about|what were we (?:talking|discussing|chatting) about|what did we (?:discuss|cover|talk about)|recap (?:our|the|this) (?:conversation|chat|talk)|remind me what we (?:talked|spoke|discussed|were talking)|what (?:have|did) i ask(?:ed)? (?:you )?(?:about )?(?:before|earlier|recently|so far|today|lately|last time)|what did i (?:ask|say|want) (?:you )?(?:earlier|before|last time)")
WORKSPACE = _WORDS(r"status of|state of|roadmap (?:for|of|on)|what(?:'s| is) (?:left|open|next|pending) (?:on|in|for)|in (?:my|the) workspace|my (?:workspace|repos?|projects?|codebase)|which (?:repo|project)|what does (?:the )?\S+ (?:repo|project) do|changelog (?:for|of)")
STOP_WORDS = frozenset('the a an of to in on at for with and or is are was were be been it its this that these those from by as about into over under between i you we they he she my your our their me us him her what which who whom how when where why do does did can could would should will shall might may not no yes please tell explain describe define'.split())
TAIL = re.compile(r"[\s?.!,]+$")


def _workspace_query(text):
    """'what is the status of milo?' -> 'milo'; the trigger words are not search terms."""
    core = WORKSPACE.sub(' ', TAIL.sub('', ' '.join(text.split())))
    stripped = STOP_TOPIC.sub('', ' '.join(core.split()), count=1).strip(' ,.?')
    if not stripped or all(word.lower() in STOP_WORDS for word in stripped.split()):
        # "What is in my workspace?" leaves only "is"; the roadmaps answer that question.
        return 'projects roadmap status'
    return stripped[:MAX_QUERY]


def _topic(text):
    """'what is the capital of Mongolia?' -> 'capital of Mongolia'."""
    core = TAIL.sub('', ' '.join(text.split()))
    core = re.sub(r"\s+(?:please|milo|for me|again|right now|today|lately|these days|nowadays)$", '', core, flags=re.I)
    stripped = STOP_TOPIC.sub('', core, count=1).strip()
    if len(stripped) < 3:
        stripped = core
    return stripped[:MAX_QUERY]


def _live_query(text):
    """Live facts search the web, so the query keeps enough of the sentence to mean something:
    'What's Bitcoin trading at?' -> 'Bitcoin price', not 'Bitcoin trading at'."""
    topic = re.sub(r'\s+(?:at|in|on|of|for|to)$', '', _topic(text), flags=re.I).strip()
    topic = re.sub(r'\btrading$', 'price', topic, flags=re.I)
    if len(topic.split()) < 2:
        topic = re.sub(r"^(?:what(?:'s| is| are)|how(?:'s| is)|who(?:'s| is))\s+", '', ' '.join(text.split()), flags=re.I)
    return topic.strip().rstrip('?.!')[:MAX_QUERY]


CLOCK_ASK = re.compile(
    r"^(?:(?:hey |ok |okay )?milo[,!]?\s+)?(?:(?:can|could) you tell me\s+)?(?:please\s+)?"
    r"(?:what(?:'s| is)? (?:the )?(?:current )?(?:time|date|day)(?: is it| of the week is it| today| right now| now)?"
    r"|what time is it(?: right now| now)?|what day (?:is it|of the week is it)(?: today)?|what(?:'s| is) today(?:'s date)?"
    r"|do you know what time it is|do you have the time)"
    r"(?:\s+(?:in|for)\s+(?P<place>[A-Za-z .'-]{2,40}))?\s*[?.!]*$",
    re.I,
)
CITY_ZONES = {
    'tokyo': 'Asia/Tokyo', 'japan': 'Asia/Tokyo', 'london': 'Europe/London', 'uk': 'Europe/London',
    'paris': 'Europe/Paris', 'berlin': 'Europe/Berlin', 'rome': 'Europe/Rome', 'madrid': 'Europe/Madrid',
    'amsterdam': 'Europe/Amsterdam', 'moscow': 'Europe/Moscow', 'new york': 'America/New_York',
    'rochester': 'America/New_York', 'boston': 'America/New_York', 'toronto': 'America/Toronto',
    'chicago': 'America/Chicago', 'denver': 'America/Denver', 'phoenix': 'America/Phoenix',
    'los angeles': 'America/Los_Angeles', 'la': 'America/Los_Angeles', 'san francisco': 'America/Los_Angeles',
    'seattle': 'America/Los_Angeles', 'vancouver': 'America/Vancouver', 'mexico city': 'America/Mexico_City',
    'sao paulo': 'America/Sao_Paulo', 'são paulo': 'America/Sao_Paulo', 'buenos aires': 'America/Argentina/Buenos_Aires',
    'sydney': 'Australia/Sydney', 'melbourne': 'Australia/Melbourne', 'auckland': 'Pacific/Auckland',
    'beijing': 'Asia/Shanghai', 'shanghai': 'Asia/Shanghai', 'hong kong': 'Asia/Hong_Kong', 'taipei': 'Asia/Taipei',
    'seoul': 'Asia/Seoul', 'singapore': 'Asia/Singapore', 'bangkok': 'Asia/Bangkok', 'mumbai': 'Asia/Kolkata',
    'delhi': 'Asia/Kolkata', 'india': 'Asia/Kolkata', 'dubai': 'Asia/Dubai', 'cairo': 'Africa/Cairo',
    'johannesburg': 'Africa/Johannesburg', 'lagos': 'Africa/Lagos', 'honolulu': 'Pacific/Honolulu',
    'anchorage': 'America/Anchorage', 'utc': 'UTC', 'gmt': 'UTC',
}


def clock_answer(text, now=None):
    """'What time is it?' and 'What's the current time in Tokyo?' come from the real clock, never
    from the model, which has no clock and confidently invents one."""
    match = CLOCK_ASK.match(' '.join((text or '').split()))
    if not match:
        return None
    import datetime
    import zoneinfo
    place = (match.group('place') or '').strip().lower().rstrip('?.!')
    moment = now or datetime.datetime.now().astimezone()
    where = ''
    if place:
        zone = CITY_ZONES.get(place)
        if zone is None:
            return None
        moment = moment.astimezone(zoneinfo.ZoneInfo(zone))
        where = ' in ' + match.group('place').strip().rstrip('?.!')
    time_text = moment.strftime('%I:%M %p').lstrip('0')
    day_text = moment.strftime('%A, %B ') + str(moment.day)
    lowered = text.lower()
    if re.search(r'\b(?:date|day|today)\b', lowered) and not re.search(r'\btime\b', lowered):
        spoken = f"Today is {day_text}, {moment.year}{where}."
    else:
        spoken = f"It's {time_text} on {day_text}{where}."
    return {'kind': 'clock', 'spoken': spoken, 'expression': 'clock' + where, 'result': moment.isoformat(timespec='minutes')}


FOLLOWUP_PRONOUN = re.compile(r"\b(?:he|she|it|they|him|her|his|hers|its|their|theirs|them|there|that one|this one|that place|the same)\b", re.I)
NAME_RUN = re.compile(r"[A-Z][\w'-]+(?:\s+(?:da|de|van|von|of|the|and|[A-Z][\w'-]+))*")
FOLLOWUP_SHORT = re.compile(r"^(?:and|what about|how about|and what about)\b", re.I)


def followup_topic(text, history):
    """The previous user question's topic when this turn leans on a pronoun ('When was he born?')
    or opens with 'and ...', so a lookup query can carry it; None otherwise."""
    if not history or not text:
        return None
    if not (FOLLOWUP_PRONOUN.search(text) or FOLLOWUP_SHORT.match(text.strip())):
        return None
    def last(role):
        return next((m.get('content') for m in reversed(history)
                     if isinstance(m, dict) and m.get('role') == role and isinstance(m.get('content'), str)), None)
    answer = last('assistant') or ''
    # The answer usually names the thing the pronoun points at ('Ernest Hemingway wrote it').
    runs = [run for run in NAME_RUN.findall(answer[:400])
            if run.split()[0] not in ('I', 'My', 'The', 'It', 'That', 'This', 'You', 'Milo', 'Yes', 'No')]
    if runs:
        return ' '.join(runs[:2]).rstrip('?.!,')
    previous = last('user')
    if not previous:
        return None
    topic = _topic(previous).strip().rstrip('?.!')
    terms = [t.rstrip('?.!,') for t in _key_terms(topic) if t.lower() not in STOP_WORDS][:4]
    return ' '.join(terms) if terms else (topic or None)


def _carry(query, carry):
    """Drop the pronoun from the query and lead with the carried subject."""
    stripped = ' '.join(FOLLOWUP_PRONOUN.sub(' ', query or '').split())
    return ' '.join((carry + ' ' + stripped).split())[:MAX_QUERY]


PROBE_BOOK_ORDER = ('wikipedia', 'wiktionary', 'archlinux', 'wikibooks', 'wikivoyage', 'unix.stackexchange')


def _book_rank(book):
    return next((i for i, prefix in enumerate(PROBE_BOOK_ORDER) if book.startswith(prefix)), len(PROBE_BOOK_ORDER))


def _title_matches(title, term):
    """A suggestion counts when the term is the title, or the title after its namespace."""
    want = ' '.join(term.lower().split())
    for candidate in (title, title.split('/')[-1], title.split(':')[-1]):
        got = ' '.join(candidate.lower().replace('_', ' ').split())
        if got == want or got.startswith(want + ' (') or got.startswith(want + ', '):
            return True
    return False


def _key_terms(topic):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'+#.-]*", topic) if w.lower() not in STOP_WORDS]
    words.sort(key=lambda w: (-(w[0].isupper()), -len(w)))
    terms = []
    for word in words[:3]:
        terms.append(word)
        if len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
            terms.append(word[:-1])  # 'hamstrings' also tries the title 'Hamstring'
    return terms


def analyse(text):
    """Shape signals for one utterance; pure, cheap, and unit-testable."""
    clean = ' '.join((text or '').split())
    lower = clean.lower()
    topic = _topic(clean)
    signals = {
        'small_talk': bool(SMALL_TALK.match(clean)) or len(lower) < 4,
        'command': bool(COMMAND.match(clean)),
        'math': bool(MATH.search(clean)),
        'opinion': bool(OPINION.search(clean)),
        'conversational': bool(CONVERSATIONAL.search(clean)),
        # Judged on the topic, so 'tell me about X' and 'how do I X' are not about the user.
        'personal': bool(PERSONAL.search(topic)),
        'question': bool(QUESTION_START.match(clean)),
        'factual_lead': bool(FACTUAL_LEAD.match(clean)),
        'fact_verb': bool(FACT_VERBS.search(clean)),
        'how_to': bool(HOW_TO.match(clean)),
        'tech': bool(TECH.search(clean)),
        'year': bool(YEAR.search(clean)),
        'proper_noun': bool(PROPER.search(clean)),
        'words': len(lower.split()),
    }
    return signals


def _band(signals):
    """'lookup', 'maybe' or 'chat' from the shape alone."""
    if signals['small_talk'] or signals['command'] or signals['math'] or signals['conversational']:
        return 'chat', 'small talk, a command, or arithmetic'
    if signals['opinion']:
        return 'chat', 'asks for an opinion or advice'
    knowledge = signals['factual_lead'] or signals['fact_verb'] or signals['tech'] or signals['year'] or signals['proper_noun']
    if signals['how_to'] and signals['tech']:
        return 'lookup', 'how-to about a technical subject'
    if (signals['question'] or signals['factual_lead']) and knowledge and not signals['personal']:
        return 'lookup', 'factual question with a named subject'
    if (signals['question'] or signals['factual_lead']) and not signals['personal']:
        return 'maybe', 'question without a clear subject'
    if signals['question'] and knowledge and signals['personal']:
        return 'maybe', 'question about the user with a factual subject'
    if signals['how_to']:
        return 'maybe', 'how-to without a technical subject'
    if knowledge and signals['words'] <= 6 and not signals['personal']:
        return 'maybe', 'short named subject'
    return 'chat', 'conversation'


RECAP_TURNS = 8
RECAP_QUESTION_CHARS = 70


def recap_answer(path=None, limit=RECAP_TURNS, now=None):
    """A spoken recap of the last questions in the turn ledger, newest first.

    Shaped like a calc answer ({'kind', 'expression', 'spoken'}) so the engine speaks it
    without a model call. Reads the ledger every time; it is a few hundred rows at most.
    """
    import turn_ledger
    ledger = turn_ledger.ledger_path() if path is None else path
    rows = []
    try:
        with open(ledger, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except (OSError, TypeError):
        rows = []
    seen, picked = set(), []
    for row in reversed(rows):
        question = ' '.join(str(row.get('question') or '').split()).rstrip('?.! ')
        if not question or row.get('cancelled') or RECAP.search(question) or row.get('app') == 'eval':
            continue
        key = re.sub(r'[^a-z0-9]+', ' ', question.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        if len(question) > RECAP_QUESTION_CHARS:
            question = question[:RECAP_QUESTION_CHARS].rsplit(' ', 1)[0] + '...'
        picked.append((question, row.get('at')))
        if len(picked) >= limit:
            break
    if not picked:
        spoken = "I don't have any earlier conversations on record yet, so this is where we start."
    else:
        first, rest = picked[0], picked[1:]
        spoken = 'Most recently you asked: ' + first[0]
        if rest:
            spoken += '. Before that: ' + '; '.join(q for q, _ in rest)
        spoken += '.'
        since = _spoken_age(picked[-1][1], now)
        if since:
            spoken += ' That goes back ' + since + '.'
    return {'kind': 'recap', 'expression': 'last %d questions' % len(picked), 'spoken': spoken, 'count': len(picked)}


def _spoken_age(stamp, now=None):
    import datetime
    if not stamp:
        return ''
    try:
        then = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ''
    now = now or datetime.datetime.now().astimezone()
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 2:
        return 'about a minute'
    if minutes < 90:
        return 'about %d minutes' % minutes
    hours = minutes // 60
    if hours < 36:
        return 'about %d hours' % hours
    return 'about %d days' % (hours // 24)


class TitleProbe:
    """Title-only Kiwix suggestions across the big books, bounded to about 30 ms."""

    def __init__(self, base_url=None, fetch=None, budget=PROBE_BUDGET_S):
        self.base_url = (os.environ.get('MILO_KIWIX_URL', KIWIX_URL)
                         if base_url is None else base_url).rstrip('/')
        self.fetch = fetch or self._fetch
        self.budget = budget
        self._books = (0.0, [])
        self._slow = {}  # book -> monotonic time of its last budget miss

    def _fetch(self, path, timeout):
        with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
            return response.read().decode('utf-8', 'replace')

    def books(self):
        checked, books = self._books
        if time.monotonic() - checked < 600 and books:
            return books
        try:
            catalog = self.fetch('/catalog/v2/entries?count=100', 1.0)
            books = list(dict.fromkeys(PROBE_BOOKS_RE.findall(catalog)))
            books.sort(key=_book_rank)  # the local-disk encyclopedia first, the slow-drive books last
        except Exception:
            books = []
        self._books = (time.monotonic(), books)
        return books

    def _hits(self, book, term):
        try:
            raw = self.fetch('/suggest?content=' + urllib.parse.quote(book) + '&term=' + urllib.parse.quote(term) + '&count=3', self.budget)
            rows = json.loads(raw)
        except Exception:
            self._slow[book] = time.monotonic()
            return []
        return [row.get('value', '') for row in rows if row.get('kind') == 'path' and _title_matches(row.get('value', ''), term)]

    def run(self, topic):
        """{'hits': [...], 'books': n, 'ms': int}; an empty catalog means the probe is off."""
        started = time.monotonic()
        now = time.monotonic()
        books = [b for b in self.books() if now - self._slow.get(b, -PROBE_COOLDOWN_S) >= PROBE_COOLDOWN_S]
        terms = list(dict.fromkeys([topic] + _key_terms(topic)))[:4]
        hits = []
        if books and terms:
            # The whole topic across the books first, then the key terms. A few requests in
            # flight at once: a cold book on the slow drive must not queue the fast ones
            # behind it, and the first matching title ends the probe. Stragglers are
            # abandoned at the budget instead of stretching the turn.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=PROBE_WORKERS)
            futures = [pool.submit(self._hits, book, term) for term in terms for book in books]
            try:
                for future in concurrent.futures.as_completed(futures, timeout=self.budget):
                    hits.extend(future.result())
                    if hits:
                        break
            except concurrent.futures.TimeoutError:
                pass
            pool.shutdown(wait=False, cancel_futures=True)
        return {'hits': list(dict.fromkeys(hits))[:5], 'books': len(books), 'terms': terms,
                'ms': round((time.monotonic() - started) * 1000)}


class Router:
    """probe=None builds the live Kiwix title probe; probe=False turns the probe off."""

    def __init__(self, probe=None):
        if probe is None:
            self.probe = TitleProbe() if os.environ.get('MILO_KIWIX_URL', KIWIX_URL).strip() else None
        else:
            self.probe = probe or None

    def decide(self, text, explicit_web=False, history=None):
        """A receipt: {'route', 'query', 'reason', 'band', 'probe', 'ms'}; route is chat, library, web,
        calc (with 'answer'), freshness, workspace or personal. history, when given, lets a pronoun
        follow-up ('When was he born?') carry the previous question's subject into the query."""
        started = time.monotonic()
        web_query = requested_web(text)
        explicit = requested_query(text, explicit_web)
        receipt = {'route': 'chat', 'query': None, 'reason': None, 'band': None, 'probe': None}
        calculation = None if explicit else calc_tool.answer(text)
        personal_query, personal = personal_trigger(text)
        if explicit_web:
            receipt.update(route='web', query=explicit, reason='asked for the web', band='explicit')
        elif web_query:
            receipt.update(route='web', query=web_query, reason='asked for the web', band='explicit')
        elif explicit:
            receipt.update(route='library', query=explicit, reason='explicit request', band='explicit')
        elif calculation:
            receipt.update(route='calc', reason='deterministic calculation', band='tool', answer=calculation)
        elif (clock := clock_answer(text)):
            receipt.update(route='calc', reason='the real clock', band='tool', answer=clock)
        elif RECAP.search(text):
            receipt.update(route='recap', reason='asks what was discussed before', band='tool', answer=recap_answer())
        elif SELF.search(text) and not PERSONAL_NOTE_ASK.search(text):
            receipt.update(route='chat', reason='asks about Milo itself', band='chat')
        elif OPINION.search(text) and not explicit:
            receipt.update(route='chat', reason='asks for an opinion or advice', band='chat')
        elif personal:
            receipt.update(route='personal', query=(personal_query or _topic(text))[:MAX_QUERY], reason='asked for the personal notes', band='explicit')
        elif WORKSPACE.search(text):
            receipt.update(route='workspace', query=_workspace_query(text), reason='asks about the workspace', band='explicit')
        elif NEWS.search(text) and not OPINION.search(text) and not SMALL_TALK.match(text):
            receipt.update(route='freshness', query=_topic(text), reason='asks about something recent', band='lookup')
        elif LIVE.search(text) and not OPINION.search(text) and not SMALL_TALK.match(text):
            receipt.update(route='web', query=_live_query(text), reason='live fact', band='live')
        elif RECENT.search(text) and not OPINION.search(text) and not SMALL_TALK.match(text):
            receipt.update(route='freshness', query=_topic(text), reason='asks about something recent', band='lookup')
        elif definition_term(text):
            receipt.update(route='library', query=' '.join(text.split())[:MAX_QUERY], reason='definition ask', band='lookup')
        else:
            signals = analyse(text)
            band, reason = _band(signals)
            receipt.update(band=band, reason=reason)
            carry = followup_topic(text, history) if band in ('lookup', 'maybe') else None
            if carry:
                receipt['carried'] = carry
            if band == 'lookup':
                receipt.update(route='library', query=_carry(_topic(text), carry) if carry else _topic(text))
            elif band == 'maybe' and self.probe is not None:
                topic = _carry(_topic(text), carry) if carry else _topic(text)
                probe = self.probe.run(topic)
                receipt['probe'] = probe
                if probe['hits']:
                    receipt.update(route='library', query=topic, reason=reason + '; library titles match ' + probe['hits'][0])
                else:
                    receipt['reason'] = reason + '; no library title matched'
        if receipt['query']:
            receipt['query'] = receipt['query'].strip().rstrip('?.!:,')[:MAX_QUERY] or None
        receipt['ms'] = round((time.monotonic() - started) * 1000)
        return receipt


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    offline = '--offline' in argv
    text = ' '.join(a for a in argv if a != '--offline')
    if not text:
        print('usage: router.py [--offline] <utterance>')
        return 2
    router = Router(probe=False if offline else None)
    print(json.dumps(router.decide(text), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
