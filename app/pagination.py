"""分页游标：把展开虚拟机状态打进带 HMAC 签名的无状态令牌。

游标绑定：

* 乐谱内容指纹（measures 规范化 JSON 的 SHA-256）——换谱即失效；
* ``page_size`` 与 ``max_steps`` —— 分页参数不一致即拒绝；
* 服务端密钥（``CURSOR_SECRET``）签名 —— 任何篡改都会导致校验失败。

令牌格式：``base64url(payload_json) + "." + base64url(hmac_sha256)`，
其中签名直接覆盖 payload 的 base64 文本，避免二次编码差异。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json

from .expander import VMState
from .models import Measure

CURSOR_VERSION = 1


class CursorError(Exception):
    """游标非法（格式损坏 / 签名不符 / 版本不支持 / 状态结构非法）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def measures_fingerprint(measures: list[Measure]) -> str:
    """乐谱内容指纹：与字段顺序、空白无关的规范化摘要。"""
    canonical = json.dumps(
        [m.model_dump() for m in measures],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _sign(secret: str, payload_b64: str) -> str:
    sig = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64e(sig)


def encode_cursor(
    secret: str,
    *,
    fingerprint: str,
    page_size: int,
    max_steps: int,
    state: VMState,
) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "fp": fingerprint,
        "page_size": page_size,
        "max_steps": max_steps,
        "state": state.to_json(),
    }
    payload_b64 = _b64e(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(secret, payload_b64)}"


def decode_cursor(secret: str, token: str) -> dict:
    """校验签名并解析游标；任何非法情况都抛 ``CursorError(CURSOR_INVALID)``。"""
    invalid = CursorError(
        "CURSOR_INVALID", "分页游标非法或已被篡改，请重新发起首页请求"
    )

    parts = token.split(".")
    if len(parts) != 2:
        raise invalid
    payload_b64, sig = parts

    try:
        expected = _sign(secret, payload_b64)
        matched = hmac.compare_digest(expected, sig)
    except (UnicodeEncodeError, TypeError):
        raise invalid from None
    if not matched:
        raise invalid

    try:
        payload = json.loads(_b64d(payload_b64))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise invalid from None

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise invalid

    fingerprint = payload.get("fp")
    page_size = payload.get("page_size")
    max_steps = payload.get("max_steps")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise invalid
    for value in (page_size, max_steps):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise invalid

    try:
        state = VMState.from_json(payload.get("state"))
    except ValueError:
        raise invalid from None

    return {
        "fp": fingerprint,
        "page_size": page_size,
        "max_steps": max_steps,
        "state": state,
    }
