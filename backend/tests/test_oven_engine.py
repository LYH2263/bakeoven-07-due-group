from app.services.oven_engine import (
    GroupItem,
    GroupPlanError,
    Interval,
    Occupancy,
    RecipeDurations,
    build_occupancies,
    find_conflicts,
    next_free_window,
    plan_group,
)
import pytest


def test_half_open_no_touch_conflict():
    a = Occupancy(1, Interval(0, 30), "bake", 1)
    b = Occupancy(1, Interval(30, 60), "bake", 2)
    assert find_conflicts([a], [b]) == []


def test_overlap_detected():
    recipe = RecipeDurations(20, 30)
    cand = build_occupancies(1, 9, 10, recipe)
    existing = [Occupancy(1, Interval(25, 40), "bake", 1)]
    assert find_conflicts(existing, cand)


def test_next_free_window_after_busy():
    existing = [
        Occupancy(1, Interval(0, 40), "ferment", 1),
        Occupancy(1, Interval(40, 70), "bake", 1),
    ]
    w = next_free_window(existing, 1, duration=30, search_from=0)
    assert w == Interval(70, 100)


def test_next_free_in_gap():
    existing = [
        Occupancy(1, Interval(0, 20), "bake", 1),
        Occupancy(1, Interval(80, 100), "bake", 2),
    ]
    w = next_free_window(existing, 1, duration=30, search_from=0)
    assert w == Interval(20, 50)


RECIPE = RecipeDurations(20, 30)  # total 50 minutes, two phases


def _item(code: str, start: int, due: int) -> GroupItem:
    return GroupItem(
        submitted_index=0,
        code=code,
        product_id=1,
        start_min=start,
        due_min=due,
        recipe=RECIPE,
    )


def test_group_picks_earliest_finishing_oven():
    # Oven 1 busy until 60; oven 2 free -> item must land on oven 2 ending 50.
    existing = [Occupancy(1, Interval(0, 60), "bake", 7)]
    items = [_item("A", 0, 200)]
    placements = plan_group(existing, [1, 2], items)
    assert len(placements) == 1
    assert placements[0].oven_id == 2
    assert placements[0].start_min == 0
    assert placements[0].end_min == 50


def test_group_respects_start_min_and_due_inclusive():
    # Earliest start 30 -> slot [30, 80); finishing exactly at due is allowed.
    placements = plan_group([], [1], [_item("A", 30, 80)])
    assert placements[0].start_min == 30
    assert placements[0].end_min == 80


def test_group_processes_by_due_not_submission_order():
    # Oven 2 fully blocked. Tight item Q must take oven 1 before loose item P.
    blocked_oven2 = [Occupancy(2, Interval(0, 24 * 60), "bake", 9)]
    items = [_item("P", 0, 100), _item("Q", 0, 55)]  # submitted loose-first
    placements = plan_group(blocked_oven2, [1, 2], items)
    by_code = {p.code: p for p in placements}
    assert by_code["Q"].oven_id == 1
    assert (by_code["Q"].start_min, by_code["Q"].end_min) == (0, 50)
    assert by_code["P"].oven_id == 1
    assert (by_code["P"].start_min, by_code["P"].end_min) == (50, 100)
    for p in placements:
        assert p.end_min <= {"P": 100, "Q": 55}[p.code]


def test_group_failure_reports_every_ovens_earliest_end():
    existing = [
        Occupancy(1, Interval(0, 80), "bake", 7),
        Occupancy(2, Interval(10, 90), "bake", 8),
    ]
    with pytest.raises(GroupPlanError) as excinfo:
        plan_group(existing, [1, 2], [_item("A", 0, 70)])
    err = excinfo.value
    assert err.order_index == 0
    assert err.item.code == "A"
    assert err.oven_ends == {1: 130, 2: 140}
    assert "70" in str(err)


def test_group_failure_identifies_second_item_with_simulated_load():
    # Due order processes A (due 55) first: oven 2 is blocked all day, so A
    # takes [0,50) on oven 1. B (due 100) then fails — oven 1's busy [60,200)
    # leaves only a 10-minute gap before it, oven 2 cannot fit anything.
    existing = [
        Occupancy(1, Interval(60, 200), "bake", 7),
        Occupancy(2, Interval(0, 24 * 60), "bake", 8),
    ]
    items = [_item("A", 0, 55), _item("B", 0, 100)]
    with pytest.raises(GroupPlanError) as excinfo:
        plan_group(existing, [1, 2], items)
    err = excinfo.value
    assert err.order_index == 1
    assert err.item.code == "B"
    assert err.oven_ends == {1: 250, 2: None}


def test_group_shuffled_orders_all_meet_their_due_without_overlap():
    # 3 items x 50 min over 2 ovens, generous dues; shuffle and re-plan.
    import random

    base = [_item("A", 0, 120), _item("B", 0, 120), _item("C", 0, 120)]
    dues = {"A": 120, "B": 120, "C": 120}
    rng = random.Random(42)
    seen_assignments: set[tuple[tuple[str, int], ...]] = set()
    for _ in range(8):
        shuffled = [
            GroupItem(
                submitted_index=i,
                code=it.code,
                product_id=1,
                start_min=it.start_min,
                due_min=it.due_min,
                recipe=RECIPE,
            )
            for i, it in enumerate(rng.sample(base, len(base)))
        ]
        placements = plan_group([], [1, 2], shuffled)
        assignment = tuple(sorted((p.code, p.oven_id) for p in placements))
        seen_assignments.add(assignment)
        # Every batch finishes by its own due.
        for p in placements:
            assert p.end_min <= dues[p.code]
        # Neither phase overlaps anything else on the same oven.
        simulated: list[Occupancy] = []
        for n, p in enumerate(placements):
            occs = build_occupancies(p.oven_id, n + 1, p.start_min, RECIPE)
            assert find_conflicts(simulated, occs) == []
            simulated.extend(occs)
    # Different submission orders genuinely produce different mappings.
    assert len(seen_assignments) > 1
