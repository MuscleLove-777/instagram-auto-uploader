# -*- coding: utf-8 -*-
"""Chromeのcookie保管庫から instagram.com の sessionid を抜き、
.env.instagrapi の IG_SESSIONID 行に書き込む（値は一切表示しない）。

- Chromeを閉じる必要なし（browser_cookie3 がDBをコピーして読む）。Chromeをkillもしない。
- 複数プロファイルを走査して instagram.com の sessionid を探す。
- 見つかった値は .env.instagrapi にだけ書く。標準出力には長さ等のマスク情報のみ。
"""
import glob
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env.instagrapi"
STATUS_FILE = HERE / "_grab_status.txt"


def write_status(msg: str):
    """結果を（値を出さずに）状態ファイルへ。昇格実行で標準出力が拾えない時用。"""
    try:
        STATUS_FILE.write_text(msg + "\n", encoding="utf-8")
    except Exception:
        pass


def find_sessionid():
    import browser_cookie3 as bc3
    candidates = []

    # 1) 既定の場所
    try:
        cj = bc3.chrome(domain_name="instagram.com")
        candidates.append(("default", cj))
    except Exception as e:
        print(f"default profile read error: {type(e).__name__}: {e}")

    # 2) 全プロファイルの Cookies を個別に走査
    userdata = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
    for cookiefile in glob.glob(str(userdata / "*" / "Network" / "Cookies")):
        try:
            cj = bc3.chrome(cookie_file=cookiefile, domain_name="instagram.com")
            candidates.append((cookiefile, cj))
        except Exception as e:
            print(f"profile read error [{Path(cookiefile).parent.parent.name}]: {type(e).__name__}: {e}")

    found = {}
    for src, cj in candidates:
        for c in cj:
            if c.name == "sessionid" and c.value:
                found[src] = c.value
    return found


def update_env(sessionid: str) -> bool:
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    replaced = False
    for ln in lines:
        if ln.strip().startswith("IG_SESSIONID=") or ln.strip().startswith("IG_SESSIONID ="):
            out.append(f"IG_SESSIONID={sessionid}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"IG_SESSIONID={sessionid}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


def main():
    try:
        found = find_sessionid()
    except Exception as e:
        msg = f"EXTRACT_FAILED: {type(e).__name__}: {e}"
        print(msg)
        write_status(msg)
        return 1
    if not found:
        msg = "sessionid_found: False (instagram.com のログインcookieが見つかりませんでした)"
        print(msg)
        write_status(msg)
        return 2
    # 複数プロファイルで見つかった場合は最長値を採用（通常は同一）
    best = max(found.values(), key=len)
    ok = update_env(best)
    msg = f"sessionid_found: True  profiles_hit: {len(found)}  value_len: {len(best)}  written_to_env: {ok}"
    print(msg)
    write_status(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
