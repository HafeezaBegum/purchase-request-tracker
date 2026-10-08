"""Score an extractor against hand-labeled samples.

    python -m eval.run_eval              # rules extractor (free, offline)
    python -m eval.run_eval --llm        # Claude extractor (needs ANTHROPIC_API_KEY)

Two numbers matter most:
- field accuracy: did we extract the right value?
- missing-field detection: when a field is NOT in the request, did we leave it
  null instead of inventing one? (A guessed deadline is worse than a blank one.)
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from app.extract import extract_llm, extract_rules
from app.models import REQUIRED_FIELDS

TODAY = date(2026, 10, 8)  # fixed so relative dates ("by Friday") are reproducible
SAMPLES = Path(__file__).with_name("samples.jsonl")


def field_match(field: str, expected, got) -> bool:
    if expected is None or got is None:
        return expected is None and got is None
    if field == "item":  # lenient: the key words must appear
        return str(expected).lower() in str(got).lower()
    if field == "quantity":
        return abs(float(expected) - float(got)) < 1e-6
    return str(expected).lower() == str(got).lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    args = ap.parse_args()
    extractor = extract_llm if args.llm else extract_rules

    samples = [json.loads(line) for line in SAMPLES.read_text(encoding="utf-8").splitlines() if line.strip()]
    correct = {f: 0 for f in REQUIRED_FIELDS}
    absent_total = absent_kept_null = 0
    perfect = 0
    failures = []

    for s in samples:
        got = extractor(s["text"], TODAY).model_dump(mode="json")
        exp = s["expected"]
        ok_all = True
        for f in REQUIRED_FIELDS:
            ok = field_match(f, exp[f], got[f])
            correct[f] += ok
            ok_all &= ok
            if exp[f] is None:
                absent_total += 1
                absent_kept_null += got[f] is None
            if not ok:
                failures.append(f"  {f:<10} expected={exp[f]!r:<28} got={got[f]!r}   <- {s['text'][:60]}")
        perfect += ok_all

    n = len(samples)
    print(f"Extractor: {'llm' if args.llm else 'rules'}   samples: {n}\n")
    for f in REQUIRED_FIELDS:
        print(f"  {f:<12} {correct[f]:>2}/{n}  ({correct[f] / n:.0%})")
    print(f"\n  all fields correct:        {perfect}/{n} ({perfect / n:.0%})")
    print(f"  missing-field detection:   {absent_kept_null}/{absent_total} absent fields left null (no guessing)")
    if failures:
        print("\nMisses:")
        print("\n".join(failures))


if __name__ == "__main__":
    main()
