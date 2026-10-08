"""Turn free-text requests into structured fields.

Two extractors share one output shape (`Extracted`):
- `extract_rules`: deterministic regex rules. Always available, no network.
- `extract_llm`: Claude with a JSON schema. Used when ANTHROPIC_API_KEY is set.

`extract()` tries the LLM first and falls back to rules on any failure, so an
API outage degrades accuracy instead of taking the intake form down.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

from pydantic import ValidationError

from .models import Extracted

log = logging.getLogger("ops.extract")

MODEL = os.getenv("OPS_MODEL", "claude-opus-5-5")

# Canonical unit -> spellings people actually type.
UNITS = {
    "m": ["meters", "meter", "metres", "metre", "m"],
    "ft": ["feet", "foot", "ft"],
    "lb": ["pounds", "pound", "lbs", "lb"],
    "kg": ["kilograms", "kilogram", "kgs", "kg"],
    "spool": ["spools", "spool"],
    "reel": ["reels", "reel"],
    "pcs": ["pieces", "piece", "pcs", "pc", "units", "unit", "each", "ea"],
    "box": ["boxes", "box"],
}
_UNIT_LOOKUP = {s: canon for canon, spellings in UNITS.items() for s in spellings}
_UNIT_RE = "|".join(sorted(_UNIT_LOOKUP, key=len, reverse=True))

DEPARTMENTS = ["production", "purchasing", "maintenance", "quality", "shipping",
               "receiving", "finance", "hr", "engineering", "safety", "it"]

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_QTY_RE = re.compile(rf"(\d[\d,]*(?:\.\d+)?)\s*({_UNIT_RE})\b", re.I)
_ITEM_AFTER_QTY_RE = re.compile(
    rf"\d[\d,]*(?:\.\d+)?\s*(?:{_UNIT_RE})\b\s*(?:of\s+)?(.+?)"
    r"(?=\s+(?:by|before|for|asap|no later|due|needed|to\s+be|from)\b|[.,;!?]|$)",
    re.I,
)


def _parse_deadline(text: str, today: date) -> date | None:
    t = text.lower()
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        year = int(m[3]) if m[3] else today.year
        year = year + 2000 if year < 100 else year
        try:
            d = date(year, int(m[1]), int(m[2]))
        except ValueError:
            return None
        return d if m[3] or d >= today else d.replace(year=year + 1)
    if m := re.search(rf"\b({'|'.join(MONTHS)})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t):
        try:
            d = date(today.year, MONTHS.index(m[1]) + 1, int(m[2]))
        except ValueError:
            return None
        return d if d >= today else d.replace(year=today.year + 1)
    if re.search(r"\btoday\b|\beod\b|end of (the )?day", t):
        return today
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if m := re.search(rf"\b(next\s+)?({'|'.join(WEEKDAYS)})\b", t):
        days_ahead = (WEEKDAYS.index(m[2]) - today.weekday()) % 7 or 7
        if m[1]:
            days_ahead += 7 if days_ahead < 7 else 0
        return today + timedelta(days=days_ahead)
    if m := re.search(r"\bin\s+(\d+)\s+(day|week)s?\b", t):
        n = int(m[1]) * (7 if m[2] == "week" else 1)
        return today + timedelta(days=n)
    # "ASAP", "end of next week", "soon" etc. are deliberately NOT guessed.
    return None


def extract_rules(text: str, today: date | None = None) -> Extracted:
    today = today or date.today()
    data: dict = {}

    if m := _QTY_RE.search(text):
        data["quantity"] = float(m[1].replace(",", ""))
        data["unit"] = _UNIT_LOOKUP[m[2].lower()]
    if m := _ITEM_AFTER_QTY_RE.search(text):
        item = m[1].strip(" -")
        if item and not re.fullmatch(r"(?:it|them|those|these|that|more)", item, re.I):
            data["item"] = item
    data["deadline"] = _parse_deadline(text, today)

    lowered = text.lower()
    for dept in DEPARTMENTS:
        # "IT" must be uppercase, otherwise "need it today" reads as the IT dept.
        found = re.search(r"\bIT\b", text) if dept == "it" else re.search(rf"\b{dept}\b", lowered)
        if found:
            data["department"] = {"hr": "HR", "it": "IT"}.get(dept, dept.title())
            break

    return Extracted(**data)


SYSTEM_PROMPT = """You extract purchasing/production requests at a copper conductor manufacturer.
Return only facts stated in the request. If a field is not stated or is ambiguous, return null.
Never invent a quantity, unit, deadline, or department.
- item: the material or product, including spec details stated (gauge, alloy, plating). Null if vague ("some wire").
- quantity: a positive number. unit: one of m, ft, lb, kg, spool, reel, pcs, box (convert spellings).
- deadline: ISO date (YYYY-MM-DD) resolved against today's date. "ASAP", "soon", or a vague range -> null.
- department: the requesting department (Production, Purchasing, Maintenance, Quality, Shipping,
  Receiving, Finance, HR, Engineering, Safety, IT). Null if not stated."""

_NULLABLE_STR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "item": _NULLABLE_STR,
        "quantity": {"type": ["number", "null"]},
        "unit": {"type": ["string", "null"], "enum": [*UNITS, None]},
        "deadline": _NULLABLE_STR,
        "department": _NULLABLE_STR,
    },
    "required": ["item", "quantity", "unit", "deadline", "department"],
    "additionalProperties": False,
}


def extract_llm(text: str, today: date | None = None, client=None) -> Extracted:
    """Extract with Claude. Raises on any API, refusal, or validation problem."""
    import anthropic

    today = today or date.today()
    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": f"Today is {today.isoformat()} ({today:%A}).\n\n<request>\n{text}\n</request>",
        }],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"unexpected stop_reason: {response.stop_reason}")
    raw = next(b.text for b in response.content if b.type == "text")
    return Extracted.model_validate(json.loads(raw))


def extract(text: str, today: date | None = None) -> tuple[Extracted, str]:
    """Return (fields, method). Method is 'llm' or 'rules'."""
    if os.getenv("ANTHROPIC_API_KEY") and os.getenv("OPS_DISABLE_LLM") != "1":
        try:
            return extract_llm(text, today), "llm"
        except (ValidationError, json.JSONDecodeError, StopIteration) as e:
            log.warning("LLM output failed validation, using rules: %s", e)
        except Exception as e:  # network, rate limit, refusal: degrade, don't fail intake
            log.warning("LLM extraction failed (%s), using rules", type(e).__name__)
    return extract_rules(text, today), "rules"


QUESTIONS = {
    "item": "What exactly is needed (material, gauge/size, any spec)?",
    "quantity": "How much is needed?",
    "unit": "What unit is that in (meters, feet, lb, spools...)?",
    "deadline": "What date is it needed by?",
    "department": "Which department is this for?",
}


def clarification_for(missing: list[str]) -> str | None:
    """Draft the follow-up message a buyer would send back to the requester."""
    if not missing:
        return None
    if "quantity" in missing and "unit" in missing:
        missing = [f for f in missing if f != "unit"]
    lines = [QUESTIONS[f] for f in missing]
    return "Thanks for the request. Before we can order, could you confirm:\n- " + "\n- ".join(lines)
