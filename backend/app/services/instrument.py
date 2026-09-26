"""仪器管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "instrument"
REQUIRED_FIELDS = ["仪器编号", "仪器名称", "型号规格"]

IN_USE = "在用"
PENDING_CALIBRATION = "待校准"
CALIBRATING = "校准中"
DISABLED = "已停用"

STATUS_ORDER = [IN_USE, PENDING_CALIBRATION, CALIBRATING, DISABLED]
ACTIVE_STATUSES = {IN_USE, PENDING_CALIBRATION, CALIBRATING}
TERMINAL_STATUSES = {DISABLED}
# 老版本可能已有“已报废”等终态；不提供新流转，但档案继续保留可查。
LEGACY_READONLY_STATUSES = {"已报废"}

START_CALIBRATION = "发起校准"
COMPLETE_CALIBRATION = "完成校准"
DISABLE_INSTRUMENT = "停用仪器"
ACTIONS = [START_CALIBRATION, COMPLETE_CALIBRATION, DISABLE_INSTRUMENT]

# 同一个“发起校准”动作覆盖校准流程的两个相邻节点：
# 在用仪器先进入待校准；再次发起时才正式转入校准中。
TRANSITIONS: dict[str, dict[str, str]] = {
    START_CALIBRATION: {
        IN_USE: PENDING_CALIBRATION,
        PENDING_CALIBRATION: CALIBRATING,
    },
    COMPLETE_CALIBRATION: {CALIBRATING: IN_USE},
    DISABLE_INSTRUMENT: {
        IN_USE: DISABLED,
        PENDING_CALIBRATION: DISABLED,
        CALIBRATING: DISABLED,
    },
}

MISSING_VERSION = "MISSING_VERSION"
INVALID_ACTION = "INVALID_ACTION"
INVALID_VERSION = "INVALID_VERSION"
STATE_CONFLICT = "STATE_CONFLICT"
VERSION_CONFLICT = "VERSION_CONFLICT"
NOT_FOUND = "NOT_FOUND"


class InstrumentService:
    def __init__(self) -> None:
        self._data_lock = threading.RLock()
        self._entry_locks: dict[int, threading.RLock] = {}
        self._inflight: set[tuple[int, str]] = set()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        if status and status not in STATUS_ORDER:
            raise ValueError(f"状态「{status}」不在允许范围：{'、'.join(STATUS_ORDER)}")

        with self._data_lock:
            rows = [self._view(entry) for entry in store.rows(MODULE)]
            if keyword:
                rows = [row for row in rows if keyword in str(row.get("仪器编号", ""))]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._data_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None
            return self._view(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        with self._data_lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = IN_USE
            entry["仪器状态"] = IN_USE
            entry["version"] = 1
            entry["pending"] = True
            entry["abnormal"] = False
            entry["history"] = [
                self._history_item(
                    action="登记建档",
                    from_status=None,
                    to_status=IN_USE,
                    remark="检测仪器档案建立",
                    version=1,
                )
            ]
            rows.append(entry)
            return self._view(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, str | None]:
        lock = self._lock_for(entry_id)
        lock.acquire()
        try:
            if store.find(MODULE, entry_id) is None:
                return None, f"检测仪器 {entry_id} 不存在或已归档", NOT_FOUND
            if action not in ACTIONS:
                return None, f"动作「{action}」不属于仪器档案可执行范围", INVALID_ACTION

            operation = (entry_id, action)
            with self._data_lock:
                if operation in self._inflight:
                    return None, f"动作「{action}」正在提交，请勿重复操作", "DUPLICATE_SUBMISSION"
                self._inflight.add(operation)
        finally:
            lock.release()

        try:
            with lock:
                with self._data_lock:
                    entry = store.find(MODULE, entry_id)
                    if entry is None:
                        return None, f"检测仪器 {entry_id} 不存在或已归档", NOT_FOUND

                    self._ensure_entry(entry)
                    current_version = int(entry.get("version", 1))
                    current_status = str(entry.get("status"))

                    target_status = TRANSITIONS[action].get(current_status)
                    if target_status is None:
                        return (
                            None,
                            f"检测仪器当前为「{current_status}」，不能执行「{action}」，请刷新后确认最新状态",
                            STATE_CONFLICT,
                        )

                    if expected_version is None:
                        return None, "缺少档案版本号，请刷新页面后重新提交", MISSING_VERSION
                    if expected_version != current_version:
                        return (
                            None,
                            "仪器档案已被其他人更新，请刷新页面后重试（当前状态："
                            f"{current_status}，版本：{current_version}）",
                            VERSION_CONFLICT,
                        )

                    next_version = current_version + 1
                    history = entry.setdefault("history", [])
                    history.append(
                        self._history_item(
                            action=action,
                            from_status=current_status,
                            to_status=target_status,
                            remark=remark or action,
                            version=next_version,
                        )
                    )
                    entry["status"] = target_status
                    entry["仪器状态"] = target_status
                    entry["version"] = next_version
                    entry["pending"] = target_status not in TERMINAL_STATUSES
                    entry["abnormal"] = target_status in TERMINAL_STATUSES
                    return self._view(entry), self._success_message(action, current_status, target_status), None
        finally:
            with self._data_lock:
                self._inflight.discard(operation)

    def available_actions(self, status: str) -> list[str]:
        return [action for action in ACTIONS if status in TRANSITIONS[action]]

    def _lock_for(self, entry_id: int) -> threading.RLock:
        with self._data_lock:
            return self._entry_locks.setdefault(entry_id, threading.RLock())

    def _ensure_entry(self, entry: dict[str, Any]) -> None:
        """补齐初始化前已经存在的老数据，不删除、不回写旧状态。"""
        status = str(entry.get("status") or "")
        if not status:
            status = IN_USE
        entry["status"] = status

        is_terminal = status in TERMINAL_STATUSES or status in LEGACY_READONLY_STATUSES
        entry["仪器状态"] = status
        entry.setdefault("version", 1)
        entry["pending"] = status in ACTIVE_STATUSES
        entry["abnormal"] = is_terminal

        history = entry.setdefault("history", [])
        if not isinstance(history, list) or not history:
            entry["history"] = [
                self._history_item(
                    action="历史档案导入",
                    from_status=None,
                    to_status=status,
                    remark="保留既有仪器档案与当前状态",
                    version=int(entry["version"]),
                )
            ]

    def _view(self, entry: dict[str, Any]) -> dict[str, Any]:
        self._ensure_entry(entry)
        view = dict(entry)
        view["history"] = [dict(item) for item in entry.get("history", [])]
        view["available_actions"] = self.available_actions(str(entry.get("status")))
        return view

    @staticmethod
    def parse_version(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("版本号必须是不小于 1 的整数")
        if isinstance(value, int):
            version = value
        elif isinstance(value, str) and value.strip().isdigit():
            version = int(value.strip())
        else:
            raise ValueError("版本号必须是不小于 1 的整数")
        if version < 1:
            raise ValueError("版本号必须是不小于 1 的整数")
        return version

    @staticmethod
    def _history_item(
        *,
        action: str,
        from_status: str | None,
        to_status: str,
        remark: str,
        version: int,
    ) -> dict[str, Any]:
        return {
            "seq": version,
            "version": version,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "remark": remark,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _success_message(action: str, from_status: str, to_status: str) -> str:
        if action == START_CALIBRATION and from_status == IN_USE:
            return "已发起校准，检测仪器进入待校准"
        if action == START_CALIBRATION and from_status == PENDING_CALIBRATION:
            return "校准已开始，检测仪器进入校准中"
        if action == COMPLETE_CALIBRATION:
            return "校准已完成，检测仪器恢复在用"
        if action == DISABLE_INSTRUMENT:
            return "检测仪器已停用"
        return f"检测仪器状态已由{from_status}切换为{to_status}"
