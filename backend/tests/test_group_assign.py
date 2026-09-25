import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.services.oven_engine import (
    DueItem,
    Interval,
    Occupancy,
    RecipeDurations,
    assign_group,
    earliest_bake_end,
)
from app.services.seed import seed_if_empty

RECIPE_30 = RecipeDurations(0, 30)


# ---------------- 引擎层 ----------------


def test_assign_group_sorted_by_due_picks_earliest_end():
    items = [
        DueItem(0, "A", RECIPE_30, release_min=480, due_min=600),
        DueItem(1, "B", RECIPE_30, release_min=480, due_min=520),
    ]
    placements, failure = assign_group([], [1, 2], items)
    assert failure is None
    by_code = {p.item.code: p for p in placements}
    # B 应出炉更早先处理：两炉并列取炉号小的 1 号
    assert (by_code["B"].oven_id, by_code["B"].start_min, by_code["B"].bake_end) == (1, 480, 510)
    # A 再排：1 号炉已占到 510，2 号炉结束更早
    assert (by_code["A"].oven_id, by_code["A"].bake_end) == (2, 510)


def test_earliest_bake_end_needs_room_for_both_segments():
    # 空档 [20,60) 只够烘烤 30 分钟，放不下发酵20+烘烤30 的整段
    existing = [
        Occupancy(1, Interval(0, 20), "bake", 1),
        Occupancy(1, Interval(60, 100), "bake", 2),
    ]
    assert earliest_bake_end(existing, 1, RecipeDurations(20, 30), 0) == 150


def test_assign_group_respects_existing_occupancy():
    existing = [Occupancy(1, Interval(480, 510), "bake", 99)]
    items = [DueItem(0, "A", RECIPE_30, 480, 600)]
    placements, failure = assign_group(existing, [1], items)
    assert failure is None
    assert (placements[0].start_min, placements[0].bake_end) == (510, 540)


def test_assign_group_failure_reports_per_oven_earliest_end():
    existing = [Occupancy(1, Interval(480, 700), "bake", 99)]
    items = [DueItem(0, "A", RECIPE_30, 480, 500)]
    placements, failure = assign_group(existing, [1, 2], items)
    assert placements == []
    assert failure.item.code == "A"
    assert failure.oven_earliest_end == {1: 730, 2: 510}


def test_assign_group_atomic_when_later_item_fails():
    # X 先占满唯一炉的首个窗口，Y 轮到时任一炉都赶不上自己的应出炉
    items = [
        DueItem(0, "X", RECIPE_30, 480, 520),
        DueItem(1, "Y", RECIPE_30, 480, 530),
    ]
    placements, failure = assign_group([], [1], items)
    assert placements == []
    assert failure.item.code == "Y"
    assert failure.oven_earliest_end == {1: 540}


def test_assign_group_shuffled_input_still_meets_due():
    base = [
        ("A", RecipeDurations(10, 20), 480, 560),
        ("B", RecipeDurations(0, 45), 500, 600),
        ("C", RecipeDurations(5, 25), 480, 590),
    ]
    for perm in itertools.permutations(base):
        items = [
            DueItem(i, code, recipe, release, due)
            for i, (code, recipe, release, due) in enumerate(perm)
        ]
        placements, failure = assign_group([], [1, 2], items)
        assert failure is None, perm
        by_code = {p.item.code: p for p in placements}
        for code, _, _, due in base:
            assert by_code[code].bake_end <= due


# ---------------- API 层 ----------------


def make_client(db_path: str) -> TestClient:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    seed_if_empty(session)  # 产品1/2/3、炉1/2/3、既有批次占炉、1 条冲突
    session.close()

    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


@pytest.fixture()
def client(tmp_path):
    with make_client(str(tmp_path / "test.db")) as c:
        yield c
    app.dependency_overrides.clear()


def test_group_assign_success_writes_whole_group(client):
    body = {
        "items": [
            {"product_id": 3, "start_min": 480, "due_min": 560, "code": "G-1"},
            {"product_id": 3, "start_min": 480, "due_min": 560, "code": "G-2"},
        ]
    }
    r = client.post("/api/batches/assign-group", json=body)
    assert r.status_code == 200, r.text
    created = r.json()
    assert len(created) == 2
    # 各自被分到炉：G-1 上 1 号炉，G-2 上 2 号炉，烘烤结束都不晚于 560
    by_code = {b["code"]: b for b in created}
    assert (by_code["G-1"]["oven_id"], by_code["G-1"]["bake_end"]) == (1, 510)
    assert (by_code["G-2"]["oven_id"], by_code["G-2"]["bake_end"]) == (2, 510)
    # 批次页能看到各自被分到的炉
    batches = {b["code"]: b for b in client.get("/api/batches").json()}
    assert batches["G-1"]["oven_label"] == "一层 1 号炉"
    assert batches["G-2"]["oven_label"] == "一层 2 号炉"
    # 甘特出现整组（每条两段）
    gantt_codes = [blk["code"] for blk in client.get("/api/gantt").json()]
    assert gantt_codes.count("G-1") == 2
    assert gantt_codes.count("G-2") == 2


def test_group_assign_failure_is_atomic_and_logged(client):
    before_batches = client.get("/api/batches").json()
    before_gantt = client.get("/api/gantt").json()
    before_conflicts = client.get("/api/conflicts").json()
    body = {
        "items": [
            {"product_id": 3, "start_min": 480, "due_min": 560, "code": "OK-1"},
            {"product_id": 3, "start_min": 480, "due_min": 500, "code": "LATE-1"},
        ]
    }
    r = client.post("/api/batches/assign-group", json=body)
    assert r.status_code == 409
    # 整组都不写入批次表，甘特一条都不新增
    assert client.get("/api/batches").json() == before_batches
    assert client.get("/api/gantt").json() == before_gantt
    # 冲突页新增一条：卡在 LATE-1（应出炉 500 最先处理），各炉最早结束 510
    after_conflicts = client.get("/api/conflicts").json()
    assert len(after_conflicts) == len(before_conflicts) + 1
    newest = after_conflicts[0]
    assert newest["batch_code"] == "LATE-1"
    assert newest["oven_id"] == 0
    assert "应出炉 500" in newest["detail"]
    assert newest["detail"].count("510") == 3  # 三座炉各自最早能结束的分钟


def test_group_assign_failure_mid_group_reports_stuck_item(client):
    # A/B/C 应出炉 520 各占满一座炉的首个窗口，D 应出炉 530 时三炉最早都 540
    body = {
        "items": [
            {"product_id": 3, "start_min": 480, "due_min": 520, "code": "A"},
            {"product_id": 3, "start_min": 480, "due_min": 520, "code": "B"},
            {"product_id": 3, "start_min": 480, "due_min": 520, "code": "C"},
            {"product_id": 3, "start_min": 480, "due_min": 530, "code": "D"},
        ]
    }
    r = client.post("/api/batches/assign-group", json=body)
    assert r.status_code == 409
    assert len(client.get("/api/batches").json()) == 3  # 只有种子批次
    newest = client.get("/api/conflicts").json()[0]
    assert newest["batch_code"] == "D"
    assert "应出炉 530" in newest["detail"]
    assert newest["detail"].count("540") == 3


def test_group_assign_shuffled_submission_meets_every_due(client):
    items = [
        {"product_id": 1, "start_min": 700, "due_min": 780, "code": "S-1"},
        {"product_id": 2, "start_min": 700, "due_min": 750, "code": "S-2"},
        {"product_id": 3, "start_min": 700, "due_min": 735, "code": "S-3"},
    ]
    due_by_code = {i["code"]: i["due_min"] for i in items}
    shuffled = [items[2], items[0], items[1]]  # 组内顺序打乱后再提交
    r = client.post("/api/batches/assign-group", json={"items": shuffled})
    assert r.status_code == 200, r.text
    created = r.json()
    assert len(created) == 3
    for b in created:
        # 分到的炉允许不同，但每条仍必须赶上自己的应出炉
        assert b["bake_end"] <= due_by_code[b["code"]]
        assert b["oven_id"] in {1, 2, 3}
