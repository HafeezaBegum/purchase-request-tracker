import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_DISABLE_LLM", "1")
    from app import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_complete_request_is_new(client):
    r = client.post("/requests", json={"text": "Production needs 200 meters of copper wire by Friday"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "new" and body["missing"] == [] and body["clarification"] is None


def test_incomplete_request_needs_info_with_question(client):
    body = client.post("/requests", json={"text": "need more wire asap"}).json()
    assert body["status"] == "needs_info"
    assert "deadline" in body["missing"] and "date" in body["clarification"]


def test_form_department_overrides_text(client):
    body = client.post("/requests", json={"text": "200 m of copper wire by Friday", "department": "Quality"}).json()
    assert body["extracted"]["department"] == "Quality" and body["status"] == "new"


def test_filling_fields_moves_to_new(client):
    rid = client.post("/requests", json={"text": "200 meters of copper wire by Friday"}).json()["id"]
    body = client.patch(f"/requests/{rid}/fields", json={"department": "Production"}).json()
    assert body["status"] == "new" and body["missing"] == []


def test_status_workflow_and_illegal_transitions(client):
    rid = client.post("/requests", json={"text": "Production needs 200 meters of copper wire by Friday"}).json()["id"]
    assert client.patch(f"/requests/{rid}/status", json={"status": "done"}).status_code == 409
    for s in ("approved", "ordered", "done"):
        assert client.patch(f"/requests/{rid}/status", json={"status": s}).json()["status"] == s
    assert client.patch(f"/requests/{rid}/status", json={"status": "rejected"}).status_code == 409


def test_cannot_mark_incomplete_request_new(client):
    rid = client.post("/requests", json={"text": "need more wire asap"}).json()["id"]
    assert client.patch(f"/requests/{rid}/status", json={"status": "new"}).status_code == 409


def test_validation_and_not_found(client):
    assert client.post("/requests", json={"text": ""}).status_code == 422
    assert client.patch("/requests/1/fields", json={"quantity": -3}).status_code == 422
    assert client.get("/requests/999").status_code == 404
    assert client.get("/requests?status=bogus").status_code == 422


def test_list_filter(client):
    client.post("/requests", json={"text": "need more wire asap"})
    client.post("/requests", json={"text": "Production needs 200 meters of copper wire by Friday"})
    assert len(client.get("/requests").json()) == 2
    assert [r["status"] for r in client.get("/requests?status=needs_info").json()] == ["needs_info"]
