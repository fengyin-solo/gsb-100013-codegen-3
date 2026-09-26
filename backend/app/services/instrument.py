"""仪器管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store

MODULE = "instrument"
REQUIRED_FIELDS = ["仪器编号", "仪器名称", "型号规格"]

# 仪器档案的状态机：在用 ⇄ 待校准 → 校准中 → 在用；已停用、已报废为终态，不再回退。
STATUS_ORDER = ["在用", "待校准", "校准中", "已停用", "已报废"]
TERMINAL_STATUSES = {"已停用", "已报废"}
FOLLOW_UP_STATUSES = {"待校准", "校准中"}

# 每个动作允许的起始状态与目标状态；表外的组合一律按状态冲突拦下。
ACTION_RULES: dict[str, dict[str, Any]] = {
    "发起校准": {"from": ["在用", "待校准"], "to": "校准中"},
    "完成校准": {"from": ["校准中"], "to": "在用"},
    "停用仪器": {"from": ["在用", "待校准", "校准中"], "to": "已停用"},
}
NEGATIVE_ACTIONS = ["停用仪器"]

# 内存仓库没有事务，用锁把「读状态-校验-写状态」收成原子操作，挡住并发提交的竞态。
_ACTION_LOCK = threading.Lock()


def allowed_actions(status: str) -> list[str]:
    """某个状态下允许执行的动作，前端按它渲染按钮、后端按它做校验。"""
    return [name for name, rule in ACTION_RULES.items() if status in rule["from"]]


class InstrumentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("仪器编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def status_summary(self) -> dict[str, int]:
        """按状态统计仪器数量，给列表页的状态卡片用；未知状态也兜底计数。"""
        summary = {status: 0 for status in STATUS_ORDER}
        for row in store.rows(MODULE):
            status = str(row.get("status") or "")
            summary[status] = summary.get(status, 0) + 1
        return summary

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["仪器状态"] = STATUS_ORDER[0]
        entry["pending"] = STATUS_ORDER[0] in FOLLOW_UP_STATUSES
        entry["abnormal"] = False
        entry["version"] = 0
        entry["history"] = []
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        expected_status: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """执行状态流转，返回 (记录, 提示语, 结果类别)。

        结果类别：ok 成功流转；repeat 重复操作（幂等，不变更）；
        missing 记录不存在；invalid 动作不存在；conflict 状态冲突。
        """
        with _ACTION_LOCK:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"检测仪器 {entry_id} 不存在或已归档", "missing"
            rule = ACTION_RULES.get(action)
            if rule is None:
                return None, f"动作「{action}」不属于仪器管理可执行范围", "invalid"
            current = str(entry.get("status") or "")
            target = str(rule["to"])
            history = entry.setdefault("history", [])
            if current == target:
                # 同一动作刚执行过才算重复提交，幂等返回；否则是按错了阶段的状态冲突。
                if history and history[-1].get("action") == action:
                    return entry, f"检测仪器已处于「{target}」，本次为重复{action}，未重复变更", "repeat"
                return None, self._conflict_message(entry_id, current, action), "conflict"
            if expected_status is not None and expected_status != current:
                return None, (
                    f"检测仪器状态已变为「{current}」，与页面显示的「{expected_status}」不一致，"
                    "请刷新列表后重试"
                ), "conflict"
            if current in TERMINAL_STATUSES:
                return None, (
                    f"检测仪器已「{current}」，生命周期已结束，不允许再执行任何动作"
                ), "conflict"
            if current not in rule["from"]:
                return None, self._conflict_message(entry_id, current, action), "conflict"
            entry["status"] = target
            entry["仪器状态"] = target
            entry["pending"] = target in FOLLOW_UP_STATUSES
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            entry["version"] = int(entry.get("version") or 0) + 1
            history.append({
                "action": action,
                "from": current,
                "to": target,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            return entry, f"检测仪器已{action}：{current} → {target}", "ok"

    @staticmethod
    def _conflict_message(entry_id: int, current: str, action: str) -> str:
        allowed = allowed_actions(current)
        options = "、".join(allowed) if allowed else "无（当前为终态）"
        return f"检测仪器 {entry_id} 当前状态「{current}」不允许{action}，可执行：{options}"
