"""演奏路径对齐：以最小编辑代价对齐两条实际演奏路径。

代价模型
--------
* ``match``   两侧当前小节号相同，代价 0；
* ``delete``  左路径特有的小节（左侧多奏），代价 1；
* ``insert``  右路径特有的小节（右侧多奏），代价 1。

对齐键为**原始小节号**；同一小节被反复演奏多次时，匹配按演奏顺序
稳定成对（第 k 次对第 k 次），各项的 occurrence 由时间轴换算给出。

平局裁决（相同代价按路径顺序稳定择优）
------------------------------------
先剥离两条路径的公共前缀 / 后缀（直接记为 match），再对中段做标准
动态规划；自 (0, 0) 向前回溯时按 **match → delete → insert** 的顺序
取第一个达到最小代价的操作（即左路径优先于右路径被消耗），因此
对齐结果确定、可复现，且与路径顺序一致。

复杂度
------
设剥离公共前后缀后两侧长度分别为 n、m，时间 O(n·m)，空间 O(n·m)
（以 4 字节整型存储）。调用方应以 ``max_cells`` 限制 n·m，
避免超大输入拖垮服务。
"""

from __future__ import annotations

from array import array

# 对齐操作：(op, 左路径下标 | None, 右路径下标 | None)
Op = tuple[str, int | None, int | None]


class DiffTooLargeError(Exception):
    """剥离公共前后缀后的对齐单元数超过服务端上限。"""

    def __init__(self, cells: int, max_cells: int):
        self.code = "DIFF_TOO_LARGE"
        self.message = (
            f"对齐规模超限：剥离公共前后缀后需要 {cells} 个对齐单元，"
            f"超过服务端上限 {max_cells}（MAX_DIFF_CELLS），"
            "请缩小乐谱规模或调大服务端配置"
        )
        super().__init__(self.message)


def _trim_common(left: list[int], right: list[int]) -> tuple[int, int]:
    """返回 (公共前缀长度, 公共后缀长度)，后缀不与前缀重叠。"""
    n, m = len(left), len(right)
    prefix = 0
    while prefix < n and prefix < m and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < n - prefix
        and suffix < m - prefix
        and left[n - 1 - suffix] == right[m - 1 - suffix]
    ):
        suffix += 1
    return prefix, suffix


def align_measures(
    left: list[int], right: list[int], max_cells: int
) -> list[Op]:
    """以最小编辑代价对齐两条小节号序列，返回按顺序排列的操作列表。

    ``max_cells`` 为剥离公共前后缀后允许的动态规划单元数上限，
    超过时抛 ``DiffTooLargeError``（由调用方映射为 422）。
    """
    n, m = len(left), len(right)
    prefix, suffix = _trim_common(left, right)

    ops: list[Op] = [("match", i, i) for i in range(prefix)]

    mid_left = left[prefix : n - suffix]
    mid_right = right[prefix : m - suffix]
    cells = len(mid_left) * len(mid_right)
    if cells > max_cells:
        raise DiffTooLargeError(cells, max_cells)
    ops.extend(_align_middle(mid_left, mid_right, prefix, prefix))

    for k in range(suffix):
        ops.append(("match", n - suffix + k, m - suffix + k))
    return ops


def _align_middle(
    a: list[int], b: list[int], off_left: int, off_right: int
) -> list[Op]:
    """对剥离公共前后缀后的中段做最小编辑代价对齐。

    ``off_left`` / ``off_right`` 为中段在原序列中的起始下标，
    用于把操作里的下标换算回原路径。
    """
    n, m = len(a), len(b)
    if n == 0:
        return [("insert", None, off_right + j) for j in range(m)]
    if m == 0:
        return [("delete", off_left + i, None) for i in range(n)]

    # dp[i][j] = 对齐 a[i:] 与 b[j:] 的最小代价（自尾向前递推）
    dp = [array("i", [0]) * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        dp[i][m] = dp[i + 1][m] + 1
    for j in range(m - 1, -1, -1):
        dp[n][j] = dp[n][j + 1] + 1
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        ai = a[i]
        for j in range(m - 1, -1, -1):
            # 相等则对角线免费匹配；不等则对角线等价于 delete+insert（代价 2）
            best = nxt[j + 1] if ai == b[j] else nxt[j + 1] + 2
            delete = nxt[j] + 1
            if delete < best:
                best = delete
            insert = row[j + 1] + 1
            if insert < best:
                best = insert
            row[j] = best

    # 自 (0, 0) 向前回溯：相同代价按 match → delete → insert 择优
    ops: list[Op] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j] and dp[i][j] == dp[i + 1][j + 1]:
            ops.append(("match", off_left + i, off_right + j))
            i += 1
            j += 1
        elif dp[i][j] == dp[i + 1][j] + 1:
            ops.append(("delete", off_left + i, None))
            i += 1
        else:
            ops.append(("insert", None, off_right + j))
            j += 1
    while i < n:
        ops.append(("delete", off_left + i, None))
        i += 1
    while j < m:
        ops.append(("insert", None, off_right + j))
        j += 1
    return ops
