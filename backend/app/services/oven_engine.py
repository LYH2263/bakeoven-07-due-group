"""Oven scheduling with half-open ferment+bake intervals and next free window."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    start: int  # minutes from day origin
    end: int  # exclusive

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class RecipeDurations:
    ferment_min: int
    bake_min: int

    @property
    def total(self) -> int:
        return self.ferment_min + self.bake_min


@dataclass(frozen=True)
class Occupancy:
    oven_id: int
    interval: Interval
    phase: str  # ferment | bake
    batch_id: int


def build_occupancies(
    oven_id: int,
    batch_id: int,
    start_min: int,
    recipe: RecipeDurations,
) -> list[Occupancy]:
    ferment = Interval(start_min, start_min + recipe.ferment_min)
    bake = Interval(ferment.end, ferment.end + recipe.bake_min)
    return [
        Occupancy(oven_id, ferment, "ferment", batch_id),
        Occupancy(oven_id, bake, "bake", batch_id),
    ]


def find_conflicts(existing: list[Occupancy], candidates: list[Occupancy]) -> list[tuple[Occupancy, Occupancy]]:
    hits: list[tuple[Occupancy, Occupancy]] = []
    for cand in candidates:
        for ex in existing:
            if ex.oven_id != cand.oven_id:
                continue
            if ex.interval.overlaps(cand.interval):
                hits.append((ex, cand))
    return hits


def next_free_window(
    existing: list[Occupancy],
    oven_id: int,
    duration: int,
    search_from: int = 0,
    search_to: int = 24 * 60,
) -> Interval | None:
    """Find earliest half-open [start, start+duration) free on oven."""
    if duration <= 0:
        return None
    busy = sorted(
        [o.interval for o in existing if o.oven_id == oven_id],
        key=lambda i: i.start,
    )
    cursor = search_from
    for iv in busy:
        if iv.end <= cursor:
            continue
        if iv.start >= cursor + duration:
            end = cursor + duration
            if end <= search_to:
                return Interval(cursor, end)
            return None
        cursor = max(cursor, iv.end)
    if cursor + duration <= search_to:
        return Interval(cursor, cursor + duration)
    return None


@dataclass(frozen=True)
class GroupItem:
    """One unscheduled batch in a group-to-assign request."""

    submitted_index: int
    code: str
    product_id: int
    start_min: int  # earliest allowed start
    due_min: int  # bake must finish no later than this
    recipe: RecipeDurations


@dataclass(frozen=True)
class GroupPlacement:
    submitted_index: int
    code: str
    product_id: int
    oven_id: int
    start_min: int
    end_min: int


class GroupPlanError(Exception):
    """Raised when one item in the group has no oven that meets its due time.

    Nothing is persisted by the planner; callers translate this into a
    conflict log entry and an atomic all-or-nothing response.
    """

    def __init__(
        self,
        order_index: int,  # 0-based position in due-ascending processing order
        item: GroupItem,
        oven_ends: dict[int, int | None],  # oven_id -> earliest finish minute (None: cannot fit that day)
    ):
        self.order_index = order_index
        self.item = item
        self.oven_ends = dict(oven_ends)
        super().__init__(
            f"成组定炉卡在第 {order_index + 1} 条（{item.code}，应出炉 {item.due_min} 分钟）"
        )


def plan_group(
    existing: list[Occupancy],
    oven_ids: list[int],
    items: list[GroupItem],
) -> list[GroupPlacement]:
    """Assign a whole group of unscheduled batches to ovens, all in memory.

    Items are processed by due_min ascending (ties keep submitted order).
    For each item, every oven gets its earliest free slot of the recipe's
    total ferment+bake duration starting no earlier than start_min; ovens
    whose slot finishes after due_min are rejected, and among the rest the
    oven with the earliest finish wins (tie: smaller oven id). Simulated
    placements join the occupancy pool so later items see them.

    Raises GroupPlanError on the first item without a feasible oven.
    """
    if not items:
        return []
    working = list(existing)
    placements: list[GroupPlacement] = []
    order = sorted(enumerate(items), key=lambda pair: (pair[1].due_min, pair[0]))
    phantom_id = -1
    for order_index, (_, item) in enumerate(order):
        oven_ends: dict[int, int | None] = {}
        best: tuple[int, int, int] | None = None  # (finish, oven_id, start)
        for oven_id in oven_ids:
            slot = next_free_window(
                working,
                oven_id,
                item.recipe.total,
                search_from=item.start_min,
            )
            oven_ends[oven_id] = slot.end if slot else None
            if slot is not None and slot.end <= item.due_min:
                candidate = (slot.end, oven_id, slot.start)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            raise GroupPlanError(order_index, item, oven_ends)
        end_min, oven_id, start_min = best
        placements.append(
            GroupPlacement(
                submitted_index=item.submitted_index,
                code=item.code,
                product_id=item.product_id,
                oven_id=oven_id,
                start_min=start_min,
                end_min=end_min,
            )
        )
        working.extend(build_occupancies(oven_id, phantom_id, start_min, item.recipe))
        phantom_id -= 1
    return placements
