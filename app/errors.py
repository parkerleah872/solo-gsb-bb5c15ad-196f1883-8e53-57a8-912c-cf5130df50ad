"""结构校验错误的数据结构。

所有错误都带原始小节号 ``measure``，没有具体小节时为 ``None``
（例如整个谱面缺少导航目标）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StructuralError:
    """一条静态结构错误或警告。"""

    code: str
    message: str
    measure: int | None = None

    def as_dict(self) -> dict:
        out: dict = {"code": self.code, "message": self.message}
        if self.measure is not None:
            out["measure"] = self.measure
        return out
