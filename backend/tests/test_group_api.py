import os

os.environ["DATABASE_URL"] = "sqlite://"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Batch, ConflictLog, Oven, Product

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False)


def _override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def _setup():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    p1 = Product(name="P1", ferment_min=20, bake_min=30)  # 50 total
    p2 = Product(name="P2", ferment_min=0, bake_min=30)  # 30 total
    o1 = Oven(label="炉甲")
    o2 = Oven(label="炉乙")
    db.add_all([p1, p2, o1, o2])
    db.commit()
    ids = (p1.id, p2.id, o1.id, o2.id)
    db.close()
    return ids


def test_group_success_persists_all_and_gantt_shows_group():
    p1, p2, o1, o2 = _setup()
    r = client.post(
        "/api/batches/group",
        json={
            "items": [
                {"product_id": p1, "start_min": 0, "due_min": 100, "code": "GA"},
                {"product_id": p2, "start_min": 0, "due_min": 100, "code": "GB"},
            ]
        },
    )
    assert r.status_code == 201, r.text
    placements = r.json()["placements"]
    by_code = {p["code"]: p for p in placements}
    assert by_code["GA"]["oven_label"] == "炉甲"
    assert by_code["GA"]["bake_end"] == 50
    assert by_code["GB"]["bake_end"] == 30
    # submitted_index follows request order regardless of due processing
    assert [p["code"] for p in placements] == ["GA", "GB"]

    gantt = client.get("/api/gantt").json()
    gantt_codes = {b["code"] for b in gantt}
    assert gantt_codes == {"GA", "GB"}
    batches = client.get("/api/batches").json()
    assert {b["code"] for b in batches} == {"GA", "GB"}


def test_group_failure_writes_nothing_and_logs_one_conflict():
    p1, p2, o1, o2 = _setup()
    # Fill both ovens so nothing starting at 0 with 50 min can finish by 60.
    client.post(
        "/api/batches",
        json={"product_id": p1, "oven_id": o1, "start_min": 10, "code": "BUSY1"},
    )
    client.post(
        "/api/batches",
        json={"product_id": p1, "oven_id": o2, "start_min": 10, "code": "BUSY2"},
    )
    r = client.post(
        "/api/batches/group",
        json={
            "items": [
                {"product_id": p1, "start_min": 0, "due_min": 55, "code": "FAIL"},
            ]
        },
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["batch_code"] == "FAIL"
    assert detail["due_min"] == 55
    assert {row["oven_label"] for row in detail["ovens"]} == {"炉甲", "炉乙"}
    for row in detail["ovens"]:
        # earliest feasible finish on a busy oven must exceed the due
        assert row["earliest_end_min"] is None or row["earliest_end_min"] > 55

    db = TestingSessionLocal()
    codes = set(db.scalars(select(Batch.code)).all())
    assert codes == {"BUSY1", "BUSY2"}  # group batch never written
    logs = db.scalars(select(ConflictLog)).all()
    assert len(logs) == 1
    assert logs[0].batch_code == "FAIL"
    assert logs[0].oven_id == 0
    assert "55" in logs[0].detail
    db.close()

    # Gantt contains no trace of the failed group.
    gantt = client.get("/api/gantt").json()
    assert {b["code"] for b in gantt} == {"BUSY1", "BUSY2"}
    conflicts = client.get("/api/conflicts").json()
    assert conflicts[0]["batch_code"] == "FAIL"


def test_group_shuffled_order_still_meets_every_due():
    p1, p2, o1, o2 = _setup()
    # Two identical 50-min jobs, tight dues; submit in reverse due order.
    payload = {
        "items": [
            {"product_id": p1, "start_min": 0, "due_min": 100, "code": "LATE"},
            {"product_id": p1, "start_min": 0, "due_min": 55, "code": "EARLY"},
        ]
    }
    r = client.post("/api/batches/group", json=payload)
    assert r.status_code == 201, r.text
    by_code = {p["code"]: p for p in r.json()["placements"]}
    assert by_code["EARLY"]["bake_end"] <= 55
    assert by_code["LATE"]["bake_end"] <= 100
