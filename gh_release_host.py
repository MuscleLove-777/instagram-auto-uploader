# -*- coding: utf-8 -*-
"""
GitHub Release 資産を「動画の一時公開URL」として使うホスト。

背景（2026-08-04）:
  匿名ホスト(catbox/litterbox)が両方落ち、catbox が返す配信ドメイン
  files.catbox.moe が DNS で引けなくなった。Instagram が動画を取得できず
  "Media upload has failed (code 0 / 2207082)" で全滅していた。
  GitHub の CDN なら取得失敗はほぼ起きない上、gh CLI の既存認証で
  鍵の追加なしに完全無人で回せる。

仕組み:
  - 固定タグ(既定 media-host)のリリースを1つだけ作り、そこへ mp4 を添付
  - 返すURLは https://github.com/<repo>/releases/download/<tag>/<name>
    （objects.githubusercontent.com へ302。IGはリダイレクトを追える）
  - 投稿が済んだら資産を削除。取りこぼしは次回実行時に日数で一括掃除
  → リポジトリに mp4 は残らず、git も太らない
"""
import os
import subprocess
import time
import requests

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
REPO = os.environ.get("IG_GH_REPO", "MuscleLove-777/instagram-auto-uploader")
TAG = os.environ.get("IG_GH_TAG", "media-host")
RETAIN_DAYS = int(os.environ.get("IG_GH_RETAIN_DAYS", "3"))
HTTP_TIMEOUT = 300

_token_cache = None


def _token():
    """.env / 環境変数 → gh CLI の順。タスクは Interactive ログオンで走るので
    gh の資格情報マネージャも読める。"""
    global _token_cache
    if _token_cache:
        return _token_cache
    for k in ("IG_GH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k, "").strip()
        if v:
            _token_cache = v
            return v
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True, timeout=30, shell=True)
        v = (r.stdout or "").strip()
        if v.startswith(("gho_", "ghp_", "ghu_", "github_pat_")):
            _token_cache = v
            return v
    except Exception:
        pass
    raise RuntimeError("GitHubトークンが取れません（gh auth login か .env の IG_GH_TOKEN）")


def _headers():
    return {"Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _get_or_create_release():
    r = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}",
                     headers=_headers(), timeout=60)
    if r.status_code == 200:
        return r.json()
    if r.status_code != 404:
        r.raise_for_status()
    c = requests.post(f"{API}/repos/{REPO}/releases", headers=_headers(), timeout=60,
                      json={"tag_name": TAG, "name": "media host (auto)",
                            "body": "Instagram投稿用の一時動画置き場。投稿後に自動削除されます。",
                            "draft": False, "prerelease": True})
    if c.status_code == 422:      # 競合（同時実行）→ 取り直す
        r = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}",
                         headers=_headers(), timeout=60)
        r.raise_for_status()
        return r.json()
    c.raise_for_status()
    return c.json()


def _delete_asset(asset_id):
    requests.delete(f"{API}/repos/{REPO}/releases/assets/{asset_id}",
                    headers=_headers(), timeout=60)


def purge_old_assets(days=None):
    """投稿失敗などで消し損ねた資産を日数で掃除（無人運用の後始末）。"""
    days = RETAIN_DAYS if days is None else days
    try:
        rel = _get_or_create_release()
    except Exception as e:
        print(f"  資産の掃除をスキップ: {e}")
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for a in rel.get("assets", []):
        try:
            ts = time.mktime(time.strptime(a.get("created_at", ""), "%Y-%m-%dT%H:%M:%SZ"))
            if ts < cutoff:
                _delete_asset(a["id"])
                n += 1
        except Exception:
            pass
    if n:
        print(f"  古い一時資産を{n}件削除")
    return n


def upload(file_path):
    """mp4 をリリース資産として上げ、公開URLを返す。"""
    rel = _get_or_create_release()
    stamp = time.strftime("%Y%m%d%H%M%S")
    base = os.path.basename(file_path)
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in base)
    name = f"{stamp}_{safe}"[-120:]

    for a in rel.get("assets", []):          # 同名が残っていれば消してから
        if a.get("name") == name:
            _delete_asset(a["id"])

    with open(file_path, "rb") as f:
        h = _headers()
        h["Content-Type"] = "video/mp4"
        r = requests.post(f"{UPLOADS}/repos/{REPO}/releases/{rel['id']}/assets",
                          headers=h, params={"name": name}, data=f, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    url = r.json().get("browser_download_url")
    if not url:
        raise RuntimeError(f"アップロード応答にURLがありません: {str(r.json())[:200]}")
    return url


def cleanup(url):
    """投稿が済んだ資産を消す。URLが自分の管轄でなければ何もしない。"""
    if not url or f"/{REPO}/releases/download/{TAG}/" not in url:
        return False
    name = url.rsplit("/", 1)[-1]
    try:
        rel = _get_or_create_release()
        for a in rel.get("assets", []):
            if a.get("name") == name:
                _delete_asset(a["id"])
                print(f"  一時資産を削除: {name}")
                return True
    except Exception as e:
        print(f"  一時資産の削除に失敗（次回掃除で回収）: {e}")
    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "purge":
        purge_old_assets(0 if "--all" in sys.argv else None)
    elif len(sys.argv) > 1:
        print(upload(sys.argv[1]))
    else:
        print("usage: python gh_release_host.py <file> | purge [--all]")
