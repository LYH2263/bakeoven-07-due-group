from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Batch, ConflictLog, Oven, Product
from app.schemas.schemas import (
    BatchCreate,
    BatchGroupCreate,
    BatchGroupOut,
    BatchOut,
    ConflictOut,
    GanttBlock,
    GroupConflictDetail,
    GroupConflictOven,
    GroupPlacementOut,
    OvenOut,
    ProductOut,
    WindowOut,
)
from app.services.oven_engine import (
    GroupItem,
    GroupPlanError,
    Occupancy,
    RecipeDurations,
    build_occupancies,
    find_conflicts,
    next_free_window,
    plan_group,
)

api_router = APIRouter()


def _recipe(p: Product) -> RecipeDurations:
    return RecipeDurations(p.ferment_min, p.bake_min)


def _hhmm(minute: int | None) -> str:
    if minute is None:
        return "当日放不下"
    return f"{minute // 60:02d}:{minute % 60:02d}"


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


@api_router.post("/batches/group", response_model=BatchGroupOut, status_code=201)
def create_batch_group(body: BatchGroupCreate, db: Session = Depends(get_db)):
    # Resolve every product first; 404 means the request itself is invalid,
    # not a scheduling failure, so no conflict log is written.
    products: list[Product] = []
    for it in body.items:
        product = db.get(Product, it.product_id)
        if not product:
            raise HTTPException(404, f"产品 {it.product_id} 不存在")
        products.append(product)

    # Fill codes up front so conflict messages and returned rows agree.
    codes: list[str] = []
    for idx, it in enumerate(body.items):
        codes.append(it.code or f"BO-G{idx:02d}")
    if len(set(codes)) != len(codes):
        raise HTTPException(400, "组内批次编码重复")
    taken = set(
        db.scalars(select(Batch.code).where(Batch.code.in_(codes))).all()
    )
    if taken:
        raise HTTPException(400, f"批次编码已存在：{sorted(taken)}")

    ovens = db.scalars(select(Oven).order_by(Oven.id)).all()
    oven_labels = {o.id: o.label for o in ovens}
    items = [
        GroupItem(
            submitted_index=idx,
            code=codes[idx],
            product_id=product.id,
            start_min=it.start_min,
            due_min=it.due_min,
            recipe=_recipe(product),
        )
        for idx, (product, it) in enumerate(zip(products, body.items))
    ]

    try:
        placements = plan_group(_all_occupancies(db), [o.id for o in ovens], items)
    except GroupPlanError as err:
        stuck = err.item
        oven_rows = [
            GroupConflictOven(
                oven_id=oven_id,
                oven_label=oven_labels[oven_id],
                earliest_end_min=end,
            )
            for oven_id, end in sorted(err.oven_ends.items())
        ]
        ends_text = "；".join(
            f"{oven_labels[row.oven_id]}最早{_hhmm(row.earliest_end_min)}"
            f"（{row.earliest_end_min if row.earliest_end_min is not None else '—'} 分）"
            for row in oven_rows
        )
        detail = (
            f"成组定炉第 {err.order_index + 1} 条 {stuck.code} 无炉可排："
            f"应出炉 {stuck.due_min} 分（{_hhmm(stuck.due_min)}）前烤不完；{ends_text}。"
            "整组未写入。"
        )
        # oven_id=0 marks a group-level failure that belongs to no single oven.
        db.add(ConflictLog(batch_code=stuck.code, oven_id=0, detail=detail))
        db.commit()
        raise HTTPException(
            409,
            detail=GroupConflictDetail(
                message=detail,
                order_index=err.order_index,
                submitted_index=stuck.submitted_index,
                batch_code=stuck.code,
                due_min=stuck.due_min,
                ovens=oven_rows,
            ).model_dump(),
        )

    # All items planned: persist the whole group in one commit.
    created: list[Batch] = []
    for plc in placements:
        batch = Batch(
            product_id=plc.product_id,
            oven_id=plc.oven_id,
            code=plc.code,
            start_min=plc.start_min,
        )
        db.add(batch)
        created.append(batch)
    db.commit()
    for batch in created:
        db.refresh(batch)

    created_by_code = {b.code: b for b in created}
    out: list[GroupPlacementOut] = []
    for plc in placements:
        batch = created_by_code[plc.code]
        product = db.get(Product, plc.product_id)
        ferment_end = plc.start_min + (product.ferment_min if product else 0)
        out.append(
            GroupPlacementOut(
                submitted_index=plc.submitted_index,
                code=plc.code,
                product_id=plc.product_id,
                product_name=product.name if product else None,
                oven_id=plc.oven_id,
                oven_label=oven_labels[plc.oven_id],
                start_min=plc.start_min,
                ferment_end=ferment_end,
                bake_end=plc.end_min,
            )
        )
    out.sort(key=lambda r: r.submitted_index)
    return BatchGroupOut(placements=out)


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
