"""Explain five frozen value states without changing the saved decision."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math


_TEXT = {
    "POSITIVE_VALUE_EVIDENCE": ("正向价值证据", "验证区间下界大于0，支持当前任务下的正向边际效用。", "可结合可信证据考虑研究使用。"),
    "NEGATIVE_VALUE_EVIDENCE": ("负向价值证据", "验证区间上界小于0，当前证据支持加入数据会增加主损失。", "当前条件下不建议采用；保留负贡献。"),
    "VALUE_UNCERTAIN": ("价值不确定", "验证区间跨0或端点为0，尚不能确定边际效用方向。", "暂缓采用并补充评价证据；这不等于价值为负或价值为0。"),
    "TASK_MODEL_UNRELIABLE": ("任务模型不可靠", "训练失败、收敛问题或验证能力关卡未通过，不能据此作采用判断。", "先检查任务定义、训练条件与模型能力。"),
    "NOT_EVALUATED": ("尚未测量", "缺少该精确版本和任务的有效测量，或当前依赖结构不支持区间。", "补充合法任务与评价资料后测量；不借用其他数据集的价值。"),
}


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def value_semantics(saved_report: Mapping | None) -> dict:
    """Return a display-only interpretation based on validation, never test.

    The original recommendation is copied verbatim, including a historical
    ``do_not_use`` action for uncertainty. The clarified hint does not mutate it.
    """
    if saved_report is not None and not isinstance(saved_report, Mapping):
        raise ValueError("SAVED_REPORT_MUST_BE_MAPPING_OR_NONE")
    report = saved_report or {}
    validation = report.get("validation") or {}
    if not isinstance(validation, Mapping):
        raise ValueError("SAVED_VALIDATION_MUST_BE_MAPPING")
    original_status = validation.get("value_status", report.get("value_status", "NOT_EVALUATED"))
    status = original_status if isinstance(original_status, str) and original_status in _TEXT else "NOT_EVALUATED"
    lower, upper = validation.get("ci_lower"), validation.get("ci_upper")
    warnings = []
    if validation.get("model_reliable") is False:
        status = "TASK_MODEL_UNRELIABLE"
    elif status in {"POSITIVE_VALUE_EVIDENCE", "NEGATIVE_VALUE_EVIDENCE", "VALUE_UNCERTAIN"}:
        if not (_finite(lower) and _finite(upper) and lower <= upper):
            status = "NOT_EVALUATED"
            warnings.append("SAVED_INTERVAL_MISSING_OR_INVALID")
        else:
            derived = "POSITIVE_VALUE_EVIDENCE" if lower > 0 else "NEGATIVE_VALUE_EVIDENCE" if upper < 0 else "VALUE_UNCERTAIN"
            if derived != status:
                warnings.append("SAVED_STATUS_INTERVAL_INCONSISTENT")
                # Do not promote a contradictory saved record to positive evidence.
                status = "VALUE_UNCERTAIN"
    label, explanation, hint = _TEXT[status]
    original_decision = deepcopy(report.get("recommendation"))
    return {"value_status": status, "original_value_status": original_status,
            "label_zh": label, "explanation_zh": explanation, "action_hint_zh": hint,
            "original_decision": original_decision, "original_recommendation": deepcopy(original_decision),
            "validation_delta": validation.get("delta_u"),
            "validation_interval": {"lower": lower, "upper": upper},
            "uses_test_for_semantics": False, "changes_saved_decision": False,
            "market_price": "NOT_EVALUATED", "factual_truth": "UNKNOWN", "warnings": warnings}
