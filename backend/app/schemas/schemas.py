from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class ProductOut(BaseModel):
    id: int
    name: str
    ferment_min: int
    bake_min: int
    model_config = {"from_attributes": True}


class OvenOut(BaseModel):
    id: int
    label: str
    capacity_note: str
    model_config = {"from_attributes": True}


class BatchOut(BaseModel):
    id: int
    product_id: int
    oven_id: int
    code: str
    start_min: int
    status: str
    product_name: str | None = None
    oven_label: str | None = None
    ferment_end: int | None = None
    bake_end: int | None = None
    model_config = {"from_attributes": True}


class BatchCreate(BaseModel):
    product_id: int
    oven_id: int
    start_min: int = Field(ge=0, le=24 * 60 - 1)
    code: str | None = None


class BatchGroupItem(BaseModel):
    product_id: int
    start_min: int = Field(ge=0, le=24 * 60 - 1)  # 最早允许开工
    due_min: int = Field(ge=0, le=24 * 60)  # 烘烤结束不晚于此
    code: str | None = None

    @field_validator("due_min")
    @classmethod
    def _due_after_start(cls, v: int, info) -> int:
        start = info.data.get("start_min")
        if start is not None and v < start:
            raise ValueError("应出炉分钟不能早于开工分钟")
        return v


class BatchGroupCreate(BaseModel):
    items: list[BatchGroupItem] = Field(min_length=1)


class GroupPlacementOut(BaseModel):
    submitted_index: int
    code: str
    product_id: int
    product_name: str | None = None
    oven_id: int
    oven_label: str | None = None
    start_min: int
    ferment_end: int
    bake_end: int


class BatchGroupOut(BaseModel):
    placements: list[GroupPlacementOut]


class GroupConflictOven(BaseModel):
    oven_id: int
    oven_label: str
    earliest_end_min: int | None  # null：当日剩余时间放不下整段


class GroupConflictDetail(BaseModel):
    message: str
    order_index: int  # 卡在哪一条（按应出炉升序的处理顺序，0 起）
    submitted_index: int  # 在提交数组中的位置（0 起）
    batch_code: str
    due_min: int
    ovens: list[GroupConflictOven]


class GanttBlock(BaseModel):
    batch_id: int
    code: str
    oven_id: int
    oven_label: str
    phase: str
    start_min: int
    end_min: int


class ConflictOut(BaseModel):
    id: int
    batch_code: str
    oven_id: int
    detail: str
    created_at: datetime
    model_config = {"from_attributes": True}


class WindowOut(BaseModel):
    oven_id: int
    oven_label: str
    start_min: int
    end_min: int
    duration_min: int
