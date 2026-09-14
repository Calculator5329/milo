"""Replay curated Milo turns, score the streamed results, and retain evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
import urllib.request
import uuid
from pathlib import Path

from router import Router
from turn_ledger import flags as ledger_flags
from turn_ledger import ledger_path


HERE = Path(__file__).resolve().parent
DEFAULT_SET = HERE / "ledger-replay.json"
DEFAULT_EVIDENCE_DIR = HERE.parent / "evidence"
DEFAULT_URL = "http://127.0.0.1:8776"
DEFAULT_ENDPOINT = "/api/demo/turn"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:12b"
DEFAULT_TIMEOUT = 120
QUICK_CATEGORIES = frozenset(("tool", "self", "opinion"))
SCORE_FIELDS = ("route_ok", "cited", "words_ok", "fast", "clean")
NOT_PHRASES = ["I don't have personal opinions", "I can't", "as an AI"]
WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def load_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_replay_set(path=DEFAULT_SET):
    items = load_json(path)
    if not isinstance(items, list):
        raise ValueError("Replay set must be a JSON list.")
    return items


def endpoint_url(url, path):
    return url.rstrip("/") + "/" + path.lstrip("/")


def post_turn(item, url=DEFAULT_URL, path=DEFAULT_ENDPOINT, timeout=DEFAULT_TIMEOUT):
    """Post one turn and reduce its NDJSON stream to the replay observation."""
    payload = {
        "id": str(uuid.uuid4()),
        "text": item["question"],
        "messages": [],
        "web": False,
    }
    request = urllib.request.Request(
        endpoint_url(url, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    observation = {
        "id": item["id"],
        "question": item["question"],
        "expect": item["expect"],
        "router": None,
        "sources": [],
        "tool": None,
        "answer": "",
        "metrics": {},
    }
    sentences = []
    errors = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            if not raw_line.strip():
                continue
            event = json.loads(raw_line.decode("utf-8"))
            kind = event.get("type")
            if kind == "router":
                observation["router"] = event.get("plan")
            elif kind == "sources":
                observation["sources"].extend(event.get("sources") or [])
            elif kind in ("tool", "action"):
                observation["tool"] = event
            elif kind == "sentence":
                sentences.append(str(event.get("text") or "").strip())
            elif kind == "done":
                observation["metrics"] = dict(event.get("metrics") or {})
            elif kind in ("error", "search_failed"):
                errors.append(str(event.get("message") or kind))
    observation["answer"] = " ".join(sentence for sentence in sentences if sentence)
    if errors:
        observation["errors"] = errors
    return observation


def ollama_chat(messages, *, ollama_url=DEFAULT_OLLAMA_URL, model=DEFAULT_MODEL,
                 timeout=DEFAULT_TIMEOUT):
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        ollama_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _judge_json(content):
    if isinstance(content, dict):
        parsed = content
    else:
        text = str(content or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError("Judge did not return a JSON object.")
            parsed = json.loads(match.group(0))
    score = int(parsed["score"])
    if score < 0 or score > 3:
        raise ValueError("Judge score is outside 0 through 3.")
    return {"score": score, "reason": str(parsed.get("reason") or "No reason supplied.").strip()}


def judge_answer(question, expect, answer, *, ollama_url=DEFAULT_OLLAMA_URL,
                 model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT):
    opinion_required = "true" if expect.get("opinion_taken") else "false"
    prompt = (
        "Judge this voice assistant answer. Return only JSON with this exact shape: "
        '{"score": 0, "reason": "short explanation"}. '
        "Use an integer score from 0 through 3. A 3 answers the question directly, is useful, "
        "and takes a clear side when an opinion is required. A 2 mostly answers with a minor gap. "
        "A 1 is substantially incomplete or evasive. A 0 refuses, dodges, or does not answer.\n\n"
        f"Category: {expect.get('category')}\n"
        f"Opinion required: {opinion_required}\n"
        f"Question: {question}\n"
        f"Answer: {answer}"
    )
    result = ollama_chat(
        [{"role": "user", "content": prompt}],
        ollama_url=ollama_url,
        model=model,
        timeout=timeout,
    )
    content = (result.get("message") or {}).get("content")
    return _judge_json(content)


def word_count(text):
    return len(WORD_RE.findall(text or ""))


def first_audio_ms(metrics):
    value = (metrics or {}).get("first_audio_ms")
    if value is None:
        value = (metrics or {}).get("first_audio_chunk_sent")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def score_observation(observation, *, judge=None, ollama_url=DEFAULT_OLLAMA_URL,
                      model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT):
    """Apply deterministic checks and the local judge to one saved observation."""
    item = dict(observation)
    expect = item["expect"]
    router = item.get("router") or {}
    expected_route = expect.get("route")
    present_citation = bool(item.get("sources") or item.get("tool"))
    answer = str(item.get("answer") or "")
    words = word_count(answer)
    audio_ms = first_audio_ms(item.get("metrics"))
    fast_limit = 1500 if expect.get("category") in QUICK_CATEGORIES else 4500
    folded = answer.casefold()
    found_phrases = [phrase for phrase in expect.get("not_phrases", []) if phrase.casefold() in folded]
    judge_call = judge or judge_answer
    try:
        judgment = judge_call(
            item["question"], expect, answer,
            ollama_url=ollama_url, model=model, timeout=timeout,
        )
        judgment = _judge_json(judgment)
    except Exception as error:
        judgment = {"score": 0, "reason": "Judge unavailable: " + str(error)}
    item.update({
        "route_ok": expected_route is None or router.get("route") == expected_route,
        "citation_present": present_citation,
        "cited": present_citation if expect.get("citation") else True,
        "word_count": words,
        "words_ok": words <= int(expect["max_words"]),
        "first_audio_ms": audio_ms,
        "fast_limit_ms": fast_limit,
        "fast": audio_ms is not None and audio_ms <= fast_limit,
        "clean": not found_phrases,
        "found_phrases": found_phrases,
        "judge": judgment["score"],
        "judge_reason": judgment["reason"],
    })
    return item


def run_replay(items, *, url=DEFAULT_URL, path=DEFAULT_ENDPOINT,
               timeout=DEFAULT_TIMEOUT, judge=None, ollama_url=DEFAULT_OLLAMA_URL,
               model=DEFAULT_MODEL):
    scored = []
    for item in items:
        try:
            observation = post_turn(item, url=url, path=path, timeout=timeout)
        except Exception as error:
            observation = {
                "id": item["id"], "question": item["question"], "expect": item["expect"],
                "router": None, "sources": [], "tool": None, "answer": "", "metrics": {},
                "errors": ["Replay request failed: " + str(error)],
            }
        scored.append(score_observation(
            observation, judge=judge, ollama_url=ollama_url, model=model, timeout=timeout,
        ))
    return scored


def _mean(values):
    return round(statistics.fmean(values), 2) if values else None


def _aggregate(items):
    audio_values = [item["first_audio_ms"] for item in items if item.get("first_audio_ms") is not None]
    result = {"items": len(items)}
    result.update({field: sum(bool(item.get(field)) for item in items) for field in SCORE_FIELDS})
    result["mean_judge"] = _mean([item.get("judge", 0) for item in items])
    result["mean_first_audio_ms"] = _mean(audio_values)
    return result


def summarize(items):
    categories = {}
    for item in items:
        category = item["expect"]["category"]
        categories.setdefault(category, []).append(item)
    return {
        "overall": _aggregate(items),
        "categories": {category: _aggregate(group) for category, group in sorted(categories.items())},
    }


def evidence_path(label, now=None):
    current = now or dt.datetime.now().astimezone()
    return DEFAULT_EVIDENCE_DIR / f"replay-{label}-{current.date().isoformat()}.json"


def _write_json(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_and_write(items, *, url=DEFAULT_URL, path=DEFAULT_ENDPOINT, label="replay",
                  output=None, timeout=DEFAULT_TIMEOUT, judge=None,
                  ollama_url=DEFAULT_OLLAMA_URL, model=DEFAULT_MODEL):
    now = dt.datetime.now().astimezone()
    scored = run_replay(
        items, url=url, path=path, timeout=timeout, judge=judge,
        ollama_url=ollama_url, model=model,
    )
    evidence = {
        "label": label,
        "at": now.isoformat(timespec="seconds"),
        "url": endpoint_url(url, path),
        "items": scored,
        "summary": summarize(scored),
    }
    _write_json(output or evidence_path(label, now), evidence)
    return evidence


def score_saved(path, *, judge=None, ollama_url=DEFAULT_OLLAMA_URL,
                model=DEFAULT_MODEL, timeout=DEFAULT_TIMEOUT):
    saved = load_json(path)
    rescored = [score_observation(
        item, judge=judge, ollama_url=ollama_url, model=model, timeout=timeout,
    ) for item in saved.get("items", [])]
    return {**saved, "items": rescored, "summary": summarize(rescored), "dry_run": True}


def _delta(after, before):
    if after is None or before is None:
        return None
    return round(after - before, 2)


def _summary_delta(after, before):
    fields = ("items",) + SCORE_FIELDS + ("mean_judge", "mean_first_audio_ms")
    return {field: _delta(after.get(field), before.get(field)) for field in fields}


def compare_results(after, before):
    before_by_id = {item["id"]: item for item in before.get("items", [])}
    item_deltas = []
    for item in after.get("items", []):
        earlier = before_by_id.get(item["id"])
        if earlier is None:
            continue
        item_deltas.append({
            "id": item["id"],
            "judge": _delta(item.get("judge"), earlier.get("judge")),
            "first_audio_ms": _delta(item.get("first_audio_ms"), earlier.get("first_audio_ms")),
            "clean": _delta(int(bool(item.get("clean"))), int(bool(earlier.get("clean")))),
        })
    after_summary = after.get("summary") or summarize(after.get("items", []))
    before_summary = before.get("summary") or summarize(before.get("items", []))
    categories = set(after_summary.get("categories", {})) & set(before_summary.get("categories", {}))
    return {
        "items": item_deltas,
        "summary": {
            "overall": _summary_delta(after_summary["overall"], before_summary["overall"]),
            "categories": {
                category: _summary_delta(
                    after_summary["categories"][category], before_summary["categories"][category],
                )
                for category in sorted(categories)
            },
        },
    }


def _category(question):
    lowered = question.casefold()
    if any(word in lowered for word in ("opinion", "better", "favorite", "should i", "should we")):
        return "opinion"
    if any(word in lowered for word in ("news", "today", "latest", "new about", "weather", "stock")):
        return "recent"
    if any(word in lowered for word in ("you do", "your personality", "talked about", "conversation history", "milo")):
        return "self"
    if lowered.startswith("define ") or " mean" in lowered:
        return "definition"
    if lowered.startswith(("remind me", "open ", "close ", "play ", "set a timer")):
        return "tool"
    return "fact"


def _new_id(question, used):
    stem = re.sub(r"[^a-z0-9]+", "-", question.casefold()).strip("-")[:48] or "ledger-turn"
    candidate = stem
    number = 2
    while candidate in used:
        candidate = f"{stem}-{number}"
        number += 1
    used.add(candidate)
    return candidate


def propose_from_ledger(base_items, path, *, flagged_only=False):
    """Return a proposed set without mutating either the ledger or curated set."""
    proposed = [dict(item) for item in base_items]
    seen = {item["question"].strip().casefold() for item in proposed}
    used_ids = {item["id"] for item in proposed}
    router = Router(probe=False)
    ledger = Path(path)
    if not ledger.exists():
        return proposed
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if flagged_only and not ledger_flags(row):
            continue
        question = str(row.get("question") or "").strip()
        if len(question.split()) < 4 or len(question) > 200:
            continue
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        category = _category(question)
        route = router.decide(question)["route"]
        proposed.append({
            "id": _new_id(question, used_ids),
            "question": question,
            "expect": {
                "route": route,
                "citation": route in ("library", "web") or category == "recent",
                "max_words": 60 if category in ("opinion", "self", "tool") else 80,
                "category": category,
                "opinion_taken": category == "opinion",
                "not_phrases": list(NOT_PHRASES),
            },
        })
    return proposed


def grow_set(base_items, ledger, grown_path, *, flagged_only=True, limit=40):
    """Merge new (flagged) ledger questions into a state-side set and return base plus grown items.

    The curated set in the repo stays untouched; the grown file is what changes night to night,
    capped at ``limit`` newest items so the nightly run stays bounded."""
    grown_path = Path(grown_path)
    try:
        grown = load_json(grown_path)
        if not isinstance(grown, list):
            grown = []
    except (OSError, ValueError):
        grown = []
    combined = list(base_items) + grown
    proposal = propose_from_ledger(combined, ledger, flagged_only=flagged_only)
    fresh = proposal[len(combined):]
    grown = (grown + fresh)[-limit:]
    _write_json(grown_path, grown)
    return list(base_items) + grown, len(fresh)


def latest_evidence(label, before=None, directory=DEFAULT_EVIDENCE_DIR):
    """The newest earlier evidence file for this label, or None."""
    paths = sorted(Path(directory).glob(f"replay-{label}-*.json"))
    if before is not None:
        paths = [p for p in paths if p.resolve() != Path(before).resolve()]
    return paths[-1] if paths else None


def print_table(evidence):
    print("id                         route cite words fast clean judge audio_ms")
    for item in evidence.get("items", []):
        marks = ["Y" if item.get(field) else "N" for field in SCORE_FIELDS]
        audio = item.get("first_audio_ms")
        print(f"{item['id'][:26]:26} {marks[0]:>5} {marks[1]:>4} {marks[2]:>5} "
              f"{marks[3]:>4} {marks[4]:>5} {item.get('judge', 0):>5} {str(audio):>8}")
    overall = evidence.get("summary", {}).get("overall", {})
    print(f"overall {overall.get('items', 0)} items, judge {overall.get('mean_judge')}, "
          f"first audio {overall.get('mean_first_audio_ms')} ms")


def print_comparison(comparison):
    print("\ncomparison                  judge audio_ms clean")
    for item in comparison["items"]:
        print(f"{item['id'][:26]:26} {str(item['judge']):>5} "
              f"{str(item['first_audio_ms']):>8} {str(item['clean']):>5}")
    overall = comparison["summary"]["overall"]
    print(f"summary delta: judge {overall['mean_judge']}, "
          f"first audio {overall['mean_first_audio_ms']} ms, clean {overall['clean']}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--path", default=DEFAULT_ENDPOINT)
    parser.add_argument("--label", default="replay")
    parser.add_argument("--set", dest="set_path", type=Path, default=DEFAULT_SET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--from-ledger", action="store_true")
    parser.add_argument("--grow", metavar="GROWN_SET", help="merge flagged ledger questions into this state file and replay them too")
    parser.add_argument("--compare-latest", action="store_true", help="compare with the newest earlier evidence of the same label")
    parser.add_argument("--flag", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--dry-run", type=Path, metavar="EVIDENCE_JSON")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.from_ledger:
        source = args.ledger or ledger_path()
        if source is None:
            print("Turn ledger is disabled.", file=sys.stderr)
            return 1
        proposal = propose_from_ledger(load_replay_set(args.set_path), source, flagged_only=args.flag)
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run:
        evidence = score_saved(
            args.dry_run, ollama_url=args.ollama_url, model=args.model, timeout=args.timeout,
        )
        if args.output:
            _write_json(args.output, evidence)
    else:
        items = load_replay_set(args.set_path)
        if args.grow:
            source = args.ledger or ledger_path()
            if source is not None:
                items, added = grow_set(items, source, args.grow)
                print(f"grown set: {len(items)} items ({added} new from the ledger)")
        previous = latest_evidence(args.label) if args.compare_latest else None
        evidence = run_and_write(
            items, url=args.url, path=args.path, label=args.label,
            output=args.output, timeout=args.timeout, ollama_url=args.ollama_url, model=args.model,
        )
        if previous is not None and not args.compare:
            args.compare = str(previous)
    print_table(evidence)
    if args.compare:
        print_comparison(compare_results(evidence, load_json(args.compare)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
