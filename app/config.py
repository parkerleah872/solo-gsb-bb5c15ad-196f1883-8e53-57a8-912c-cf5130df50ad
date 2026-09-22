"""运行期配置（全部来自环境变量，带默认值）。"""

from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    def __init__(self) -> None:
        self.app_name: str = os.getenv("APP_NAME", "乐谱反复路径展开 API")
        self.max_expansion_steps: int = int(os.getenv("MAX_EXPANSION_STEPS", "100000"))
        self.default_max_steps: int = int(os.getenv("DEFAULT_MAX_STEPS", "10000"))
        # 分页游标 HMAC 签名密钥：默认值仅供本地开发，生产部署必须覆盖
        self.cursor_secret: str = os.getenv(
            "CURSOR_SECRET", "insecure-dev-cursor-secret-change-me"
        )
        self.max_page_size: int = int(os.getenv("MAX_PAGE_SIZE", "1000"))
        # 路径对比两侧展开路径长度乘积的上限（对齐算法时间/空间均为 O(n·m)）
        self.max_diff_cells: int = int(os.getenv("MAX_DIFF_CELLS", "4000000"))

        if self.max_expansion_steps < 1:
            raise ValueError("MAX_EXPANSION_STEPS 必须为正整数")
        if self.default_max_steps > self.max_expansion_steps:
            raise ValueError("DEFAULT_MAX_STEPS 不能大于 MAX_EXPANSION_STEPS")
        if not self.cursor_secret:
            raise ValueError("CURSOR_SECRET 不能为空")
        if self.max_page_size < 1:
            raise ValueError("MAX_PAGE_SIZE 必须为正整数")
        if self.max_diff_cells < 1:
            raise ValueError("MAX_DIFF_CELLS 必须为正整数")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
