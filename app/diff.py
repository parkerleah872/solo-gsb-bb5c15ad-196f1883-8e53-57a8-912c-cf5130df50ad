"""两条实际演奏路径的最小编辑代价对齐。

对齐语义
--------
* 输入为两侧展开后的实际演奏路径（原始小节号序列，含反复造成的重复）。
* 只允许三种操作：``match``（两侧当前小节相同）、``delete``（仅左侧演奏）、
  ``insert``（仅右侧演奏）。不提供替换操作：一处“不同的小节”等价于
  delete + insert，代价为 2。
* 编辑代价 = insert + delete 的数量（即 LCS 距离），对齐结果保证代价最小。
* 相同代价时**按路径顺序稳定择优**：

  1. 两侧当前小节相同 ⇒ 直接 match（最先出现的演奏优先对齐，后续内容
     的增删不会影响前面已对齐的部分）；
  2. delete 与 insert 代价相同 ⇒ 优先 delete（先删后插，与常见 diff
     工具的惯例一致）。

  由此对齐结果完全确定、可复现，不依赖任何哈希或迭代顺序。

实现
----
先自底向上计算后缀 LCS 距离表 ``d[i][j]``（对齐 left[i:] 与 right[j:]
的最小编辑代价），再从路径起点 (0, 0) 前向回溯，按上述规则逐步生成操作。
时间 O(n·m)，空间 O(n·m)（距离表按行用 4 字节无符号整数存储）。
调用方需先用 ``MAX_DIFF_CELLS`` 限制 n·m，避免超长输入拖垮服务。
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Literal


@dataclass
class DiffOp:
    """一个对齐操作。

    ``left_index`` / ``right_index`` 为各自展开路径中的 0 基下标；
    delete 时 ``right_index`` 为 None，insert 时 ``left_index`` 为 None。
    """

    kind: Literal["match", "delete", "insert"]
    left_index: int | None
    right_index: int | None


def align_paths(left: list[int], right: list[int]) -> list[DiffOp]:
    """用最小编辑代价对齐两条演奏路径，返回按路径顺序排列的操作序列。"""
    n, m = len(left), len(right)

    # d[i][j] = 对齐 left[i:] 与 right[j:] 的最小编辑代价（后缀 LCS 距离）
    d: list[array] = [array("I", [0]) * (m + 1) for _ in range(n + 1)]
    d[n] = array("I", range(m, -1, -1))  # 左侧耗尽：剩余右侧全部 insert
    for i in range(n - 1, -1, -1):
        row = d[i]
        nxt = d[i + 1]
        row[m] = n - i  # 右侧耗尽：剩余左侧全部 delete
        li = left[i]
        for j in range(m - 1, -1, -1):
            if li == right[j]:
                row[j] = nxt[j + 1]
            else:
                delete_cost = nxt[j]
                insert_cost = row[j + 1]
                row[j] = (delete_cost + 1) if delete_cost <= insert_cost else (insert_cost + 1)

    # 从路径起点前向回溯：相同优先 match，代价相同优先 delete
    ops: list[DiffOp] = []
    i = j = 0
    while i < n and j < m:
        if left[i] == right[j]:
            ops.append(DiffOp("match", i, j))
            i += 1
            j += 1
        elif d[i + 1][j] <= d[i][j + 1]:
            ops.append(DiffOp("delete", i, None))
            i += 1
        else:
            ops.append(DiffOp("insert", None, j))
            j += 1
    while i < n:
        ops.append(DiffOp("delete", i, None))
        i += 1
    while j < m:
        ops.append(DiffOp("insert", None, j))
        j += 1
    return ops
