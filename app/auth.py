"""登录鉴权：密码哈希 + 令牌签发/校验。仅用标准库（hashlib/hmac/secrets），
不引入 passlib/PyJWT 等第三方依赖，与项目"核心逻辑纯标准库"的原则一致。

密码存储：PBKDF2-HMAC-SHA256，随机 16 字节盐，10 万次迭代，格式
"pbkdf2_sha256$<迭代次数>$<盐hex>$<哈希hex>"，可安全存于 SQLite 文本列。

令牌：自制的最小化签名令牌（非标准 JWT，但同样的"载荷+签名"思路）：
base64url(json 载荷) + "." + HMAC-SHA256(密钥, 载荷).hexdigest()
载荷含 uid/username/role/exp，服务端用 SECRET_KEY 重新计算签名比对，
篡改载荷或签名任何一处都会校验失败。有效期由 config.TOKEN_TTL_HOURS 控制。

安全边界（如实说明）：这是一个教学/演示级实现，没有令牌吊销列表、没有
刷新令牌轮换、没有防重放随机数；生产环境建议替换为标准 JWT 库或
OAuth2/session 方案，并确保 SECRET_KEY 通过环境变量设置为随机长字符串。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from . import config

PBKDF2_ITERATIONS = 100_000


class AuthError(Exception):
    """登录/令牌校验失败的统一异常，main.py 捕获后转为 401。"""


# ---------------------------------------------------------------- 密码哈希

def hash_password(password: str) -> str:
    if not password or len(password) < 6:
        raise AuthError("密码长度至少 6 位")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                       bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------- 令牌

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_b64: str) -> str:
    return hmac.new(config.SECRET_KEY.encode("utf-8"), payload_b64.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def create_token(user_id: str, username: str, role: str) -> str:
    payload = {
        "uid": user_id, "username": username, "role": role,
        "iat": int(time.time()),
        "exp": int(time.time() + config.TOKEN_TTL_HOURS * 3600),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def decode_token(token: str) -> dict:
    """校验并解出载荷；任何问题一律抛 AuthError（令牌无效/已过期）。"""
    if not token or "." not in token:
        raise AuthError("令牌格式无效")
    payload_b64, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        raise AuthError("令牌签名校验失败（可能被篡改或密钥已更换）")
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise AuthError(f"令牌载荷解析失败：{exc}")
    if payload.get("exp", 0) < time.time():
        raise AuthError("登录已过期，请重新登录")
    return payload


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise AuthError("缺少登录令牌，请先登录")
    return authorization_header[len("Bearer "):].strip()
