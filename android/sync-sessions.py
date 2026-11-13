#!/usr/bin/env python3
"""Copy only selected Chrome platform sessions to the user's USB-connected V2T app.

Cookie values exist only in process memory. A phone-specific RSA key wraps an AES-GCM
key; only the encrypted envelope is sent over adb stdin to app-private storage.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

APP = "io.github.feihaitun.v2t"
SITES = {
    "bili": ("BILI", "哔哩哔哩", ("bilibili.com",), {"SESSDATA"}),
    "x": ("X", "X / Twitter", ("x.com", "twitter.com"), {"auth_token"}),
    "xhs": ("XHS", "小红书", ("xiaohongshu.com", "rednote.com"), {"web_session"}),
    "youtube": ("YOUTUBE", "YouTube", ("youtube.com",), {"SID", "__Secure-3PSID", "SAPISID"}),
}


class QuietLogger:
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def read_platform(profile: str, domains: tuple[str, ...], login_names: set[str]) -> str:
    """Use the same domain-scoped read/decrypt approach as the desktop downloader."""
    from yt_dlp.cookies import get_cookie_decryptor

    root = Path.home() / "Library/Application Support/Google/Chrome"
    if not profile:
        profile = json.loads((root / "Local State").read_text()).get("profile", {}).get("last_used") or "Default"
    if not re.fullmatch(r"Default|Profile \d+", profile):
        raise ValueError("无效的 Chrome 配置名")
    directory = root / profile
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("找不到 Chrome 配置目录")
    database = next((p for p in (directory / "Network/Cookies", directory / "Cookies") if p.is_file()), None)
    if database is None:
        return ""
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("PRAGMA query_only=ON")
        version = int(connection.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
        rows = []
        for domain in domains:
            rows.extend(connection.execute(
                "SELECT host_key,name,value,encrypted_value,path,expires_utc,is_secure,is_httponly "
                "FROM cookies WHERE host_key=? OR host_key LIKE ?", (domain, "%." + domain)).fetchall())
    finally:
        connection.close()
    decryptor = None
    exported = []
    names = set()
    for host, name, value, encrypted, path, expires, secure, http_only in rows:
        expiry = int(expires / 1_000_000 - 11_644_473_600) if expires else 0
        if expiry and expiry <= time.time():
            continue
        if not value and encrypted:
            if decryptor is None:
                decryptor = get_cookie_decryptor(str(root), "Chrome", QuietLogger(), meta_version=version)
            value = decryptor.decrypt(encrypted)
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if not value or any(x in value for x in "\r\n\t"):
            continue
        names.add(name)
        exported.append("\t".join((
            ("#HttpOnly_" if http_only else "") + host,
            "TRUE" if host.startswith(".") else "FALSE", path or "/",
            "TRUE" if secure else "FALSE", str(expiry), name, value,
        )))
    return "# Netscape HTTP Cookie File\n" + "\n".join(exported) + "\n" if names & login_names else ""


def encrypt(public: dict, sites: list[dict], request_id: str) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    der = base64.b64decode(public["publicKey"], validate=True)
    if public.get("version") != 1 or hashlib.sha256(der).hexdigest() != public["fingerprint"]:
        raise ValueError("手机公钥不完整")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    body = json.dumps({"requestId": request_id, "sites": sites}, ensure_ascii=False).encode()
    ciphertext = AESGCM(key).encrypt(nonce, body, b"V2T-session-sync-v1")
    wrapped = serialization.load_der_public_key(der).encrypt(key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA256(), label=None))
    b64 = lambda value: base64.b64encode(value).decode()
    return json.dumps({"version": 1, "fingerprint": public["fingerprint"], "requestId": request_id,
                       "wrappedKey": b64(wrapped), "nonce": b64(nonce), "ciphertext": b64(ciphertext)}).encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", nargs="+", choices=SITES, default=list(SITES))
    parser.add_argument("--profile", default="", help="Chrome 的 Default / Profile 1 等目录名；默认最近使用的配置")
    parser.add_argument("--serial", default="", help="多台手机连接时指定 adb serial")
    args = parser.parse_args()
    adb = ["adb"] + (["-s", args.serial] if args.serial else [])
    def run(*command, data=None, check=True):
        return subprocess.run(adb + list(command), input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=check, timeout=45).stdout
    run("get-state")
    run("shell", "am", "start", "-n", f"{APP}/.MainActivity")
    public = None
    for _ in range(30):
        try:
            public = json.loads(run("exec-out", "run-as", APP, "cat", "files/session-sync-key.json"))
            break
        except (subprocess.SubprocessError, ValueError):
            time.sleep(0.5)
    if public is None:
        print("尚未准备好手机接收密钥，请先安装新版 V2T 并打开一次。", file=sys.stderr)
        return 1
    sites = []
    for name in dict.fromkeys(args.sites):
        code, label, domains, login_names = SITES[name]
        try:
            # Do not allow upstream helpers to print decrypted browser data or diagnostics.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                cookies = read_platform(args.profile, domains, login_names)
        except Exception:
            print(f"{label}：未能读取当前 Chrome 会话，手机原有资料保持不变。")
            continue
        if cookies:
            sites.append({"site": code, "cookies": cookies})
            print(f"{label}：已找到登录态，准备加密同步。")
        else:
            print(f"{label}：当前 Chrome 配置未找到登录态，跳过。")
    if not sites:
        print("没有可同步的登录态。可以指定 --profile，或在 V2T 里导入已有 Cookie。")
        return 2
    request_id = str(uuid.uuid4())
    envelope = encrypt(public, sites, request_id)
    # Command arguments contain no credentials. run-as confines the inbox to this debug app.
    # exec-in does not drain a large stdout stream: use dd, not tee. Verify the
    # complete ciphertext before atomically making it visible to the app.
    pending = "files/session-sync-inbox.pending.json"
    run("shell", "-T", "run-as", APP, "dd", f"of={pending}", data=envelope)
    expected = hashlib.sha256(envelope).hexdigest().encode()
    for _ in range(30):
        actual = run("exec-out", "run-as", APP, "sha256sum", pending, check=False).split()
        if actual and actual[0] == expected:
            break
        time.sleep(0.2)
    else:
        run("exec-out", "run-as", APP, "rm", "-f", pending, check=False)
        raise ValueError("USB 传输不完整")
    run("exec-out", "run-as", APP, "mv", pending, "files/session-sync-inbox.json")
    run("shell", "am", "start", "-n", f"{APP}/.MainActivity", "--ez", "sync_sessions", "true")
    for _ in range(60):
        try:
            result = json.loads(run("exec-out", "run-as", APP, "cat", "files/session-sync-result.json"))
            if result.get("requestId") == request_id:
                if not result.get("ok"):
                    print(result.get("error", "手机未完成导入，请重试。"))
                    return 1
                print(f"已保存 {len(result['imported'])} 个平台的登录态；关闭 App、重启手机仍会保留。")
                return 0
        except (subprocess.SubprocessError, ValueError):
            pass
        time.sleep(0.5)
    print("手机尚未确认导入。请查看 V2T 的状态提示后重试。")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        # Never print exception objects: browser/decryption errors can contain private data.
        print("同步未完成，请检查 USB 连接、应用版本和 Python 依赖。", file=sys.stderr)
        raise SystemExit(1)
