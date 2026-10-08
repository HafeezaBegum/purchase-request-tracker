from datetime import date
from types import SimpleNamespace

import pytest

from app.extract import clarification_for, extract, extract_llm, extract_rules

TODAY = date(2026, 10, 8)  # a Thursday


def test_full_request():
    r = extract_rules("Production needs 200 meters of copper wire by Friday.", TODAY)
    assert (r.item, r.quantity, r.unit, r.deadline, r.department) == (
        "copper wire", 200, "m", date(2026, 10, 9), "Production")
    assert r.missing_fields() == []


def test_vague_request_flags_everything():
    r = extract_rules("need more wire asap", TODAY)
    assert set(r.missing_fields()) == {"item", "quantity", "unit", "deadline", "department"}


@pytest.mark.parametrize("phrase, expected", [
    ("by tomorrow", date(2026, 10, 9)),
    ("by Monday", date(2026, 10, 12)),
    ("by Thursday", date(2026, 10, 15)),  # same weekday as today means next week
    ("due 10/20", date(2026, 10, 20)),
    ("by Oct 15", date(2026, 10, 15)),
    ("needed 2026-11-02", date(2026, 11, 2)),
    ("in 2 weeks", date(2026, 10, 22)),
    ("asap", None),
    ("end of next week", None),  # ambiguous: flagged, not guessed
    ("by 13/45", None),  # invalid date doesn't crash
])
def test_deadlines(phrase, expected):
    assert extract_rules(f"5 spools of wire {phrase}", TODAY).deadline == expected


def test_unit_normalization_and_commas():
    r = extract_rules("please order 1,500 lbs of copper rod", TODAY)
    assert (r.quantity, r.unit, r.item) == (1500, "lb", "copper rod")


def test_lowercase_it_is_not_the_it_department():
    # Regression: found by the eval set ("need it today" was tagged IT).
    assert extract_rules("out of die lubricant, need it today", TODAY).department is None
    assert extract_rules("IT needs 6 barcode scanners", TODAY).department == "IT"


def test_clarification_mentions_only_missing():
    msg = clarification_for(["deadline", "department"])
    assert "date" in msg and "department" in msg and "How much" not in msg
    assert clarification_for([]) is None


def _fake_client(text, stop_reason="end_turn"):
    resp = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp)))


def test_llm_output_is_validated():
    good = '{"item":"copper wire","quantity":200,"unit":"m","deadline":"2026-10-09","department":"Production"}'
    assert extract_llm("x", TODAY, client=_fake_client(good)).deadline == date(2026, 10, 9)
    with pytest.raises(Exception):  # negative quantity rejected by the model schema
        extract_llm("x", TODAY, client=_fake_client(good.replace("200", "-5")))
    with pytest.raises(RuntimeError):
        extract_llm("x", TODAY, client=_fake_client(good, stop_reason="refusal"))


def test_falls_back_to_rules_when_llm_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("OPS_DISABLE_LLM", raising=False)

    def boom(*a, **kw):
        raise ConnectionError("network down")

    monkeypatch.setattr("app.extract.extract_llm", boom)
    fields, method = extract("Production needs 200 meters of copper wire by Friday", TODAY)
    assert method == "rules" and fields.quantity == 200
