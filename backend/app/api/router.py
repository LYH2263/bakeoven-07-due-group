from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Batch, ConflictLog, Oven, Product
from app.schemas.schemas import (
    BatchCreate,
    BatchOut,
    ConflictOut,
    GanttBlock,
    GroupAssignIn,
    OvenOut,
    ProductOut,
    WindowOut,
)
from app.services.oven_engine import (
    DueItem,
    Occupancy,
    RecipeDurations,
    assign_group,
    build_occupancies,
    find_conflicts,
    next_free_window,
)

api_router = APIRouter()


def _recipe(p: Product) -> RecipeDurations:
    return RecipeDurations(p.ferment_min, p.bake_min)


def _all_occupancies(db: Session) -> list[Occupancy]:
    batches = db.scalars(select(Batch)).all()
    out: list[Occupancy] = []
    for b in batches:
        p = db.get(Product, b.product_id)
        if not p:
            continue
        out.extend(build_occupancies(b.oven_id, b.id, b.start_min, _recipe(p)))
    return out


def _batch_out(db: Session, b: Batch) -> BatchOut:
    p = db.get(Product, b.product_id)
    o = db.get(Oven, b.oven_id)
    ferment_end = b.start_min + (p.ferment_min if p else 0)
    bake_end = ferment_end + (p.bake_min if p else 0)
    return BatchOut(
        id=b.id,
        product_id=b.product_id,
        oven_id=b.oven_id,
        code=b.code,
        start_min=b.start_min,
        status=b.status,
        product_name=p.name if p else None,
        oven_label=o.label if o else None,
        ferment_end=ferment_end,
        bake_end=bake_end,
    )


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/products", response_model=list[ProductOut])
def products(db: Session = Depends(get_db)):
    return db.scalars(select(Product).order_by(Product.id)).all()


@api_router.get("/ovens", response_model=list[OvenOut])
def ovens(db: Session = Depends(get_db)):
    return db.scalars(select(Oven).order_by(Oven.id)).all()


@api_router.get("/batches", response_model=list[BatchOut])
def batches(db: Session = Depends(get_db)):
    rows = db.scalars(select(Batch).order_by(Batch.start_min)).all()
    return [_batch_out(db, b) for b in rows]


@api_router.post("/batches", response_model=BatchOut)
def create_batch(body: BatchCreate, db: Session = Depends(get_db)):
    product = db.get(Product, body.product_id)
    oven = db.get(Oven, body.oven_id)
    if not product or not oven:
        raise HTTPException(404, "产品或炉位不存在")
    recipe = _recipe(product)
    candidates = build_occupancies(oven.id, -1, body.start_min, recipe)
    existing = _all_occupancies(db)
    hits = find_conflicts(existing, candidates)
    code = body.code or f"BO-{body.start_min}"
    if hits:
        ex, cand = hits[0]
        detail = (
            f"与批次#{ex.batch_id} 的 {ex.phase} 段重叠："
            f"[{cand.interval.start},{cand.interval.end})"
        )
        db.add(ConflictLog(batch_code=code, oven_id=oven.id, detail=detail))
        db.commit()
        raise HTTPException(409, detail)
    batch = Batch(
        product_id=product.id,
        oven_id=oven.id,
        code=code,
        start_min=body.start_min,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return _batch_out(db, batch)


@api_router.post("/batches/assign-group", response_model=list[BatchOut])
def assign_group_batches(body: GroupAssignIn, db: Session = Depends(get_db)):
    """按应出炉成组定炉：整组成功才写入批次；任一找不到炉则整组不落库。"""
    ovens = db.scalars(select(Oven).order_by(Oven.id)).all()
    if not ovens:
        raise HTTPException(400, "没有可用炉位")
    products = {p.id: p for p in db.scalars(select(Product)).all()}
    taken = set(db.scalars(select(Batch.code)).all())
    items: list[DueItem] = []
    for idx, raw in enumerate(body.items):
        product = products.get(raw.product_id)
        if not product:
            raise HTTPException(404, f"产品不存在: {raw.product_id}")
        base = raw.code or f"BO-{raw.start_min}"
        code, n = base, 1
        while code in taken:
            n += 1
            code = f"{base}-{n}"
        taken.add(code)
        items.append(
            DueItem(
                index=idx,
                code=code,
                recipe=_recipe(product),
                release_min=raw.start_min,
                due_min=raw.due_min,
            )
        )
    placements, failure = assign_group(
        _all_occupancies(db), [o.id for o in ovens], items
    )
    if failure:
        stuck = failure.item
        per_oven = "、".join(
            f"{o.label} 最早 {failure.oven_earliest_end[o.id]}"
            if failure.oven_earliest_end[o.id] is not None
            else f"{o.label} 日内无可排"
            for o in ovens
        )
        detail = f"成组定炉失败：卡在 {stuck.code}（应出炉 {stuck.due_min}）；{per_oven}"
        db.add(ConflictLog(batch_code=stuck.code, oven_id=0, detail=detail))
        db.commit()
        raise HTTPException(409, detail)
    by_index = {raw_idx: raw for raw_idx, raw in enumerate(body.items)}
    created: list[Batch] = []
    for pl in placements:
        batch = Batch(
            product_id=by_index[pl.item.index].product_id,
            oven_id=pl.oven_id,
            code=pl.item.code,
            start_min=pl.start_min,
        )
        db.add(batch)
        created.append(batch)
    db.commit()
    for b in created:
        db.refresh(b)
    return [_batch_out(db, b) for b in created]


@api_router.get("/gantt", response_model=list[GanttBlock])
def gantt(db: Session = Depends(get_db)):
    blocks: list[GanttBlock] = []
    for b in db.scalars(select(Batch).order_by(Batch.start_min)).all():
        p = db.get(Product, b.product_id)
        o = db.get(Oven, b.oven_id)
        if not p or not o:
            continue
        for occ in build_occupancies(b.oven_id, b.id, b.start_min, _recipe(p)):
            blocks.append(
                GanttBlock(
                    batch_id=b.id,
                    code=b.code,
                    oven_id=o.id,
                    oven_label=o.label,
                    phase=occ.phase,
                    start_min=occ.interval.start,
                    end_min=occ.interval.end,
                )
            )
    return blocks


@api_router.get("/conflicts", response_model=list[ConflictOut])
def conflicts(db: Session = Depends(get_db)):
    return db.scalars(select(ConflictLog).order_by(ConflictLog.id.desc())).all()


@api_router.get("/windows", response_model=list[WindowOut])
def windows(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "产品不存在")
    duration = product.ferment_min + product.bake_min
    existing = _all_occupancies(db)
    out: list[WindowOut] = []
    for oven in db.scalars(select(Oven).order_by(Oven.id)).all():
        w = next_free_window(existing, oven.id, duration, search_from=8 * 60, search_to=22 * 60)
        if w:
            out.append(
                WindowOut(
                    oven_id=oven.id,
                    oven_label=oven.label,
                    start_min=w.start,
                    end_min=w.end,
                    duration_min=duration,
                )
            )
    return out
