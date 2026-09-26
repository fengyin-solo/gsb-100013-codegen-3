"""仪器管理接口：维护检测仪器，覆盖发起校准、完成校准、停用仪器等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.instrument import (
    INVALID_VERSION,
    STATUS_ORDER,
    InstrumentService,
)

router = APIRouter(prefix="/api/instrument", tags=["仪器管理"])

service = InstrumentService()

LIST_FIELDS = ["仪器编号", "仪器名称", "型号规格", "所属实验室", "校准周期", "上次校准日", "下次校准日", "仪器状态"]
STATUSES = STATUS_ORDER


def no_store(response: Response) -> Response:
    """离开页面再回来必须重新拉取档案，避免浏览器缓存旧状态。"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.get("", response_model=PageResult[dict])
def list_entries(
    response: Response,
    keyword: str | None = Query(default=None, description="按仪器编号检索"),
    status: str | None = Query(default=None, description="在用、待校准、校准中、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按仪器编号与状态过滤仪器档案；没有数据时返回空页，不报错。"""
    no_store(response)
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if page < 1:
        raise HTTPException(status_code=400, detail="页码必须从 1 开始")
    try:
        items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(response: Response) -> dict[str, Any]:
    """导出仪器档案清单：返回当前档案数据，并禁止调用方复用过期状态。"""
    no_store(response)
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "instrument", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, response: Response) -> dict[str, Any]:
    """读取单条检测仪器明细；不存在时给出可读的错误说明。"""
    no_store(response)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测仪器 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测仪器，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}", code="MISSING_FIELDS")
    return ActionResult(ok=True, message="检测仪器已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测仪器执行当前状态允许的动作，并做版本冲突校验。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        expected_version = InstrumentService.parse_version(payload.values.get("expected_version"))
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc), code=INVALID_VERSION)

    entry, message, code = service.run_action(
        entry_id,
        action,
        expected_version=expected_version,
        remark=payload.remark,
    )
    if entry is None:
        return ActionResult(ok=False, message=message, code=code)
    return ActionResult(ok=True, message=message, entry=entry)
