"""冷链探头读数判定：摄氏温度不超过 8 为合格，否则超温。"""

# 冷媒余量估算：每次成功提交读数，对应批次消耗固定 1 个单位
UNITS_PER_SUBMISSION = 1


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"


def estimate_remaining(start_balance: int, consumed_units: int) -> int:
    """服务端统一重算批次余量：起始余量 - 已消耗单位数（与消耗明细对账）。"""
    return start_balance - consumed_units
