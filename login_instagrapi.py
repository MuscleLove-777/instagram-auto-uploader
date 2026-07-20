# -*- coding: utf-8 -*-
"""instagrapi ログイン / セッション確立モジュール。

- 認証情報は同ディレクトリの .env.instagrapi から読む（値は絶対に画面に出さない）。
- 既存 session_instagrapi.json があれば再利用し、生きていればそのまま使う。
- 失効/未作成なら新規ログインしてセッションを保存する。
- 2段階認証: IG_2FA_CODE（手動6桁）優先、無ければ IG_TOTP_SECRET から自動生成。
- 端末チャレンジ: IG_CHALLENGE_CODE を使う。

単体実行 = セッション確立の確認用。
投稿側は get_logged_in_client() を import して使う（無人投稿の入口）。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env.instagrapi"
SESSION_FILE = HERE / "session_instagrapi.json"


def load_env():
    """.env.instagrapi を os.environ に流し込む（値は出力しない）。"""
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        # 値を囲むクォートを除去（"pass" / 'pass' で囲む事故を吸収）
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        os.environ.setdefault(key.strip(), val)


def _build_client():
    from instagrapi import Client

    cl = Client()
    # ログイン〜2FA送信の間に30秒コードが失効しないよう待ちは短めに
    cl.delay_range = [1, 2]
    # 日本の端末・ロケールを名乗る（初回ログインの anti-bot 弾きを下げる）
    for setter, arg in (
        (getattr(cl, "set_locale", None), "ja_JP"),
        (getattr(cl, "set_country", None), "JP"),
        (getattr(cl, "set_country_code", None), 81),
        (getattr(cl, "set_timezone_offset", None), 9 * 3600),
    ):
        try:
            if setter:
                setter(arg)
        except Exception:
            pass

    def challenge_code_handler(username, choice):
        code = os.environ.get("IG_CHALLENGE_CODE", "").strip()
        if code:
            return code
        raise RuntimeError(
            "CHALLENGE_REQUIRED: 端末確認コードが必要です。"
            "メール/SMSに届いた6桁を IG_CHALLENGE_CODE に入れて再実行してください。"
        )

    cl.challenge_code_handler = challenge_code_handler
    return cl


def _fresh_login(cl, username, password):
    """新規ログイン（2FA対応）。成功でセッションを保存。例外は呼び出し側へ。"""
    two_fa = os.environ.get("IG_2FA_CODE", "").strip()
    totp = os.environ.get("IG_TOTP_SECRET", "").strip()
    if two_fa:
        cl.login(username, password, verification_code=two_fa)
    elif totp:
        import pyotp
        totp = totp.replace(" ", "").upper()  # スペース区切り/小文字混入を吸収
        cl.login(username, password, verification_code=pyotp.TOTP(totp).now())
    else:
        cl.login(username, password)
    cl.dump_settings(str(SESSION_FILE))


def get_logged_in_client():
    """ログイン済み Client を返す。無人投稿の共通入口。

    優先: ①既存セッション再利用 → ②失効なら再ログイン。
    認証情報が無い/2FA未設定などは例外で落とす（呼び出し側で握る）。
    """
    load_env()
    username = os.environ.get("IG_LOGIN_USERNAME", "").strip()
    password = os.environ.get("IG_LOGIN_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("NO_CREDENTIALS: .env.instagrapi に IG_LOGIN_USERNAME / IG_LOGIN_PASSWORD が必要です。")

    cl = _build_client()

    if SESSION_FILE.exists():
        try:
            cl.load_settings(str(SESSION_FILE))
            cl.login(username, password)  # settings があればセッション優先で検証
            cl.get_timeline_feed()         # 生存確認
            return cl, "session_reuse"
        except Exception as e:
            print(f"既存セッション失効/無効のため再ログインします: {type(e).__name__}")
            cl = _build_client()

    # ブラウザの既存セッション(sessionid)で入る本命ルート。
    # パスワード/2FA/初回IP弾きを全部回避できる。IG_SESSIONID があれば最優先。
    sessionid = os.environ.get("IG_SESSIONID", "").strip()
    if sessionid:
        cl.login_by_sessionid(sessionid)
        cl.get_timeline_feed()  # 生存確認
        cl.dump_settings(str(SESSION_FILE))
        return cl, "sessionid_login"

    _fresh_login(cl, username, password)
    return cl, "fresh_login"


def main():
    try:
        cl, mode = get_logged_in_client()
    except Exception as e:
        name = type(e).__name__
        if name == "TwoFactorRequired":
            print("TWO_FACTOR_REQUIRED: 2段階認証が有効です。"
                  "認証アプリ/SMSの6桁を IG_2FA_CODE に入れるか、"
                  "認証アプリのシークレットを IG_TOTP_SECRET に設定して再実行してください。")
            return 3
        print(f"LOGIN_FAILED: {name}: {e}")
        return 1
    who = os.environ.get("IG_LOGIN_USERNAME", "").strip()
    tag = "SESSION SAVED" if mode == "fresh_login" else "session reused"
    print(f"Login OK ({mode}). user={who}  [{tag}] -> {SESSION_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
