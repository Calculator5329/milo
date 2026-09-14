"""Choose careful cloud turns and remove their explicit trigger phrase.

Explicit phrases are: think carefully, think hard, ask the big model, use the
cloud model, use openrouter, ask openrouter, give me a thorough answer, be
thorough, and careful answer.
"""
import json
import re
import sys


_EXPLICIT_PHRASES = (
    'think carefully',
    'think hard',
    'ask the big model',
    'use the cloud model',
    'use openrouter',
    'ask openrouter',
    'give me a thorough answer',
    'be thorough',
    'careful answer',
)
_EXPLICIT_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(phrase) for phrase in _EXPLICIT_PHRASES) + r')\b',
    re.IGNORECASE,
)
_PROOF_RE = re.compile(
    r'\b(?:prove\b|(?:proof|derivation)\s+(?:of|that|for)\b|derive\b|step[\s-]+by[\s-]+step(?:\s+reasoning)?)',
    re.IGNORECASE,
)
_COMPARE_RE = re.compile(r'\bcompare\b[^?.!]*\band\b', re.IGNORECASE | re.DOTALL)
_TRADEOFF_RE = re.compile(
    r'\btrade[\s-]*offs?\b|\bpros\s+and\s+cons\b', re.IGNORECASE
)
_PROGRAM_RE = re.compile(
    r'\b(?:write|writing|create|creating|implement|implementing|build|building|code)\b'
    r'.{0,100}\b(?:script|function|class|program)\b',
    re.IGNORECASE | re.DOTALL,
)
_DEPTH_RE = re.compile(r'\b(?:in depth|in detail)\b', re.IGNORECASE)
_DEFINITION_RE = re.compile(r'^\s*what\s+(?:is|are|does)\b', re.IGNORECASE)
_WORD_RE = re.compile(r"\b[\w']+\b")


def wants_careful(question: str) -> str | None:
    """Return a short reason when a question merits the stronger model."""
    if _EXPLICIT_RE.search(question):
        return 'explicit request'
    if _DEPTH_RE.search(question):
        return 'depth requested'
    if not _DEFINITION_RE.search(question) and _PROOF_RE.search(question):
        return 'proof or structured reasoning'
    if _COMPARE_RE.search(question) and _TRADEOFF_RE.search(question):
        return 'comparison with tradeoffs'
    if _PROGRAM_RE.search(question):
        return 'programming task'
    if len(_WORD_RE.findall(question)) > 60:
        return 'long question'
    return None


def cleaned(question: str) -> str:
    """Remove explicit careful-model phrases and tidy adjacent punctuation."""
    text = _EXPLICIT_RE.sub(' ', question)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^\s*[,;:.!?-]+\s*', '', text)
    text = re.sub(r'\s+([,;:.!?])', r'\1', text)
    text = re.sub(r'[,;:]+(?=[.!?])', '', text)
    text = re.sub(r'([,;:])[,;:]+', r'\1', text)
    return text.strip()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit('usage: careful.py "<question>"')
    question = args[0]
    print(json.dumps({'reason': wants_careful(question), 'cleaned': cleaned(question)}))


if __name__ == '__main__':
    main()
