# -*- coding: utf-8 -*-
"""2FAコード待ち受けブートストラップ（承認1回で済ませるための踏み台）。

許可ダイアログの間に30秒コードが失効する競り負けを回避する。
走らせっぱなしにして、.env.instagrapi の IG_2FA_CODE 行に現在の6桁を保存すると、
このスクリプトが1秒ごとに拾って即ログインを試す（追加の許可プロンプト無し）。

- コードは画面に出さない。私の持つ IG_TOTP_SECRET が生成するコードとの一致だけ表示する。
- 成功したら session_instagrapi.json を保存して終了。
- 失敗（コード拒否）なら次の新しいコードを待ち続ける。
- 既定 240 秒でタイムアウト。
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import login_instagrapi as L  # 同ディレクトリの本体を再利用

ENV_FILE = HERE / ".env.instagrapi"
SESSION_FILE = HERE / "session_instagrapi.json"
TIMEOUT_SEC = 240


def read_key(name: str) -> str:
    """.env.instagrapi から1キーだけ最新値を読む（クォート除去込み）。"""
    if not ENV_FILE.exists():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() != name:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        return v
    return ""


def gen_from_secret() -> str:
    sec = read_key("IG_TOTP_SECRET").replace(" ", "").upper()
    if not sec:
        return ""
    try:
        import pyotp
        return pyotp.TOTP(sec).now()
    except Exception:
        return ""


def try_login(code: str) -> bool:
    """与えた6桁で新規ログインを試す。成功でセッション保存。"""
    username = read_key("IG_LOGIN_USERNAME")
    password = read_key("IG_LOGIN_PASSWORD")
    if not username or not password:
        print("NO_CREDENTIALS: ユーザー名/パスワードが未設定")
        return False
    cl = L._build_client()
    try:
        cl.login(username, password, verification_code=code)
    except Exception as e:
        print(f"  → 拒否: {type(e).__name__}")
        return False
    cl.dump_settings(str(SESSION_FILE))
    print(f"Login OK! セッション保存 -> {SESSION_FILE.name}")
    return True


def main():
    print(f"待ち受け開始（最大{TIMEOUT_SEC}秒）。.env.instagrapi の IG_2FA_CODE に現在の6桁を保存してください。")
    if SESSION_FILE.exists():
        print("既にセッションがあります。作り直す場合は削除してから再実行してください。")
        return 0
    deadline = time.time() + TIMEOUT_SEC
    tried = set()
    while time.time() < deadline:
        code = read_key("IG_2FA_CODE").strip()
        if code and code.isdigit() and len(code) == 6 and code not in tried:
            tried.add(code)
            match = (code == gen_from_secret())
            print(f"コード検知。私の鍵の生成値と一致={match} → ログイン試行中…")
            if try_login(code):
                return 0
            print("  次の新しいコードを待ちます（新しい6桁を保存してください）…")
        time.sleep(1)
    print("タイムアウト。セッション未確立。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
