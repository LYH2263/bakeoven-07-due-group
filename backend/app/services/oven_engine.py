"""Oven scheduling with half-open ferment+bake intervals and next free window."""

from __future__ import annotations

from dataclasses import dataclass

DAY_END = 24 * 60


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
class DueItem:
    """一条待成组定炉的批次。"""

    index: int  # 组内提交序号，用于回映与并列时定序
    code: str
    recipe: RecipeDurations
    release_min: int  # 开工分钟：不得更早开工
    due_min: int  # 应出炉分钟：烘烤结束不得晚于它


@dataclass(frozen=True)
class Placement:
    item: DueItem
    oven_id: int
    start_min: int
    bake_end: int


@dataclass(frozen=True)
class GroupFailure:
    item: DueItem  # 卡住的那一条
    oven_earliest_end: dict[int, int | None]  # 每座炉最早能结束的分钟（None=日内无可排）


def earliest_bake_end(
    existing: list[Occupancy],
    oven_id: int,
    recipe: RecipeDurations,
    release_min: int,
    search_to: int = DAY_END,
) -> int | None:
    """该炉上不早于 release_min 开工、两段都不重叠时，最早的烘烤结束分钟。"""
    w = next_free_window(existing, oven_id, recipe.total, search_from=release_min, search_to=search_to)
    return w.end if w else None


def assign_group(
    existing: list[Occupancy],
    oven_ids: list[int],
    items: list[DueItem],
    search_to: int = DAY_END,
) -> tuple[list[Placement], GroupFailure | None]:
    """按应出炉从早到晚逐条定炉；任一找不到炉则整组失败（placements 为空）。

    发酵+烘烤是连续整段，「两段都不重叠」等价于整段空闲，故直接复用
    next_free_window 求每座炉的最早开工/结束。
    """
    occ = list(existing)
    placements: list[Placement] = []
    for item in sorted(items, key=lambda i: (i.due_min, i.index)):
        ends = {
            oid: earliest_bake_end(occ, oid, item.recipe, item.release_min, search_to)
            for oid in oven_ids
        }
        feasible = [(end, oid) for oid, end in ends.items() if end is not None and end <= item.due_min]
        if not feasible:
            return [], GroupFailure(item=item, oven_earliest_end=ends)
        bake_end, oven_id = min(feasible)  # 烘烤结束最早；并列取炉号小者
        start = bake_end - item.recipe.total
        placements.append(Placement(item=item, oven_id=oven_id, start_min=start, bake_end=bake_end))
        occ.extend(build_occupancies(oven_id, -1, start, item.recipe))
    return placements, None
