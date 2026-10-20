"""只读 Chrome 中 douyin.com 的 Cookie；不复制整库、不导出值、不改变登录状态。

只使用 yt-dlp 的解密器处理匹配到的行，避免其通用导出函数读取所有站点。
Chrome 时间戳是自 1601 年起的微秒，传给 Playwright 前转换为 Unix 秒。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

LOGIN_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard"}


class CookieUnavailable(Exception):
    def __init__(self, status):
        self.status = status


class QuietLogger:
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def chrome_root() -> Path:
    if sys.platform != "darwin":
        raise CookieUnavailable("no_browser")
    return Path.home() / "Library/Application Support/Google/Chrome"


def load_cookies(profile: str = "", *, root: Path | None = None, decryptor_factory=None,
                 domain: str = "douyin.com", login_names: set[str] | None = None) -> list[dict]:
    if domain not in {"douyin.com", "bilibili.com"}:
        raise CookieUnavailable("bad_profile")
    login_names = login_names or LOGIN_NAMES
    root = root or chrome_root()
    if not root.is_dir():
        raise CookieUnavailable("no_browser")
    if not profile:
        try:
            state = json.loads((root / "Local State").read_text())
            profile = state.get("profile", {}).get("last_used") or "Default"
        except (OSError, ValueError):
            profile = "Default"
    if not isinstance(profile, str) or not re.fullmatch(r"Default|Profile \d+", profile):
        raise CookieUnavailable("bad_profile")
    directory = root / profile
    if directory.is_symlink() or not directory.is_dir():
        raise CookieUnavailable("bad_profile")
    db = next((p for p in (directory / "Network/Cookies", directory / "Cookies") if p.is_file()), None)
    if db is None:
        raise CookieUnavailable("no_login")
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            con.execute("PRAGMA query_only=ON")
            version = int(con.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
            # 不选择其他站点的 Cookie 行；不会读取它们的明文或密文值。
            rows = con.execute("""SELECT host_key, name, value, encrypted_value, path,
                expires_utc, is_secure, is_httponly, samesite FROM cookies
                WHERE host_key=? OR host_key LIKE ?""", (domain, '%.' + domain)).fetchall()
        finally:
            con.close()
        decryptor = None
        result = []
        now = time.time()
        for host, name, value, encrypted, path, expires, secure, httponly, samesite in rows:
            expiry = expires / 1_000_000 - 11_644_473_600 if expires else -1
            if expires and expiry <= now:
                continue
            if not value and encrypted:
                if decryptor is None:
                    if decryptor_factory is None:
                        from yt_dlp.cookies import get_cookie_decryptor
                        decryptor_factory = get_cookie_decryptor
                    decryptor = decryptor_factory(str(root), "Chrome", QuietLogger(), meta_version=version)
                value = decryptor.decrypt(encrypted)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            if not value:
                continue
            cookie = {"name": name, "value": value, "domain": host, "path": path or "/",
                      "expires": expiry, "secure": bool(secure), "httpOnly": bool(httponly)}
            if samesite in (0, 1, 2):
                cookie["sameSite"] = {0: "None", 1: "Lax", 2: "Strict"}[samesite]
            result.append(cookie)
        if not any(c["name"] in login_names for c in result):
            raise CookieUnavailable("no_login")
        return result
    except CookieUnavailable:
        raise
    except Exception:
        raise CookieUnavailable("cookie_unreadable") from None
