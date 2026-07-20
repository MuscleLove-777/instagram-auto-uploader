# -*- coding: utf-8 -*-
"""SMS 2FA でセッションを確立する待ち受け（承認1回で済む踏み台）。

1) パスワードログインを試行 → SMS 2FA が要求され、Instagram が携帯へSMSを送る。
2) .env.instagrapi の IG_2FA_CODE に届いた6桁を保存すると、これが拾って即ログイン。
3) 成功で session_instagrapi.json を保存して終了。以後は無人でセッション再利用。

SMSコードは数分有効なのでタイミング競争は無い。既定300秒待ち受け。
コードは画面に出さない。
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import login_instagrapi as L
from instagrapi.exceptions import TwoFactorRequired

ENV_FILE = HERE / ".env.instagrapi"
SESSION_FILE = HERE / "session_instagrapi.json"
STATUS = HERE / "_sms_status.txt"
TIMEOUT = 300


def status(msg):
    print(msg, flush=True)
    try:
        STATUS.write_text(msg + "\n", encoding="utf-8")
    except Exception:
        pass


def read_2fa_code():
    if not ENV_FILE.exists():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("IG_2FA_CODE=") and not s.startswith("#"):
            return s.split("=", 1)[1].strip()
    return ""


def main():
    L.load_env()
    user = os.environ.get("IG_LOGIN_USERNAME", "").strip()
    pw = os.environ.get("IG_LOGIN_PASSWORD", "").strip()
    if not user or not pw:
        status("NO_CREDENTIALS")
        return 2

    cl = L._build_client()
    try:
        cl.login(user, pw)  # 2FA無しなら即成功
        cl.dump_settings(str(SESSION_FILE))
        status("OK (2FAなしでログイン)。SESSION SAVED")
        return 0
    except TwoFactorRequired:
        status("SMS送信済み。届いた6桁を .env.instagrapi の IG_2FA_CODE に保存してください（数分有効）")
    except Exception as e:
        status(f"LOGIN_ERR: {type(e).__name__}: {e}")
        return 1

    deadline = time.time() + TIMEOUT
    tried = set()
    while time.time() < deadline:
        code = read_2fa_code()
        if code and code.isdigit() and len(code) == 6 and code not in tried:
            tried.add(code)
            status("コード検知。ログイン中…")
            try:
                cl.login(user, pw, verification_code=code)
                cl.dump_settings(str(SESSION_FILE))
                status("Login OK! SESSION SAVED")
                return 0
            except Exception as e:
                status(f"コード拒否: {type(e).__name__}。新しい6桁を保存し直してください")
        time.sleep(1)
    status("タイムアウト。セッション未確立")
    return 1


if __name__ == "__main__":
    sys.exit(main())
