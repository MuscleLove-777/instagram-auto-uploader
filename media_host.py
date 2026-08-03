# -*- coding: utf-8 -*-
"""
ローカルファイル → 公開URL 変換モジュール（鍵不要・憲法第4条準拠）

Instagram Graph API はローカルバイナリを受け取れず、
公開URL(image_url / video_url)を指定してコンテナを作る仕様。
Google Drive の uc?export=download URL は Instagram 側が取得に失敗する
（HTMLインターステイシャルが返る）ため、匿名アップロード先で
「直リンクの公開URL」を作ってから Instagram に渡す。

優先順:
  ① GitHub Release 資産（gh_release_host.py・投稿後に自動削除）
  ② catbox.moe（匿名フォールバック）
  ③ litterbox.catbox.moe（同上・2026-08時点で412/504を返すため最後）

2026-08-04: 匿名ホストが両方落ちた。catbox はアップロードこそ通るが
配信ドメイン files.catbox.moe が DNS で引けず、Instagram が動画を取得できず
"Media upload has failed (code 0 / 2207082)" で全滅していた。
主軸を GitHub Release へ移し、匿名ホストは保険として残す。

返す前に必ず「実際に取得できるURLか」を検証する（上がったつもりの死んだURLを
IGに渡さないため）。
"""
import os
import time
import requests

import gh_release_host

LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
CATBOX_API = "https://catbox.moe/user/api.php"
HTTP_TIMEOUT = 180
UPLOAD_ATTEMPTS = int(os.environ.get("IG_HOST_ATTEMPTS", "3"))
UPLOAD_BACKOFF_S = int(os.environ.get("IG_HOST_BACKOFF_S", "20"))


def _upload_litterbox(file_path, expire="72h"):
    with open(file_path, "rb") as f:
        files = {"fileToUpload": (os.path.basename(file_path), f)}
        data = {"reqtype": "fileupload", "time": expire}
        resp = requests.post(LITTERBOX_API, data=data, files=files, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"litterbox unexpected response: {url[:200]}")
    return url


def _upload_catbox(file_path):
    with open(file_path, "rb") as f:
        files = {"fileToUpload": (os.path.basename(file_path), f)}
        data = {"reqtype": "fileupload"}
        resp = requests.post(CATBOX_API, data=data, files=files, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox unexpected response: {url[:200]}")
    return url


def verify_public_url(url, expect_bytes=None, attempts=3, wait_s=10):
    """IGに渡す前に、そのURLが本当に取得できるか確かめる。
    反映待ちがあるので数回リトライ。最後まで駄目なら例外。"""
    last = ""
    for i in range(attempts):
        try:
            r = requests.get(url, headers={"Range": "bytes=0-262143"},
                             stream=True, timeout=60)
            size = r.headers.get("Content-Range", "").split("/")[-1]
            if r.status_code in (200, 206):
                if expect_bytes and size.isdigit() and int(size) != expect_bytes:
                    last = f"size mismatch {size} != {expect_bytes}"
                else:
                    r.close()
                    return True
            else:
                last = f"HTTP {r.status_code}"
            r.close()
        except Exception as e:
            last = str(e)
        if i + 1 < attempts:
            time.sleep(wait_s)
    raise RuntimeError(f"公開URLが取得できません({url}): {last}")


def cleanup_public_url(url):
    """投稿が済んだ一時ファイルの後始末（匿名ホストは自動失効なので何もしない）。"""
    try:
        return gh_release_host.cleanup(url)
    except Exception:
        return False


def upload_to_public_url(file_path):
    """ローカルファイルを公開ホストへ上げ、取得可能を確認した公開URLを返す。
    全ホスト×全リトライが失敗したときだけ例外。"""
    expect_bytes = os.path.getsize(file_path)
    errors = []
    try:
        gh_release_host.purge_old_assets()
    except Exception:
        pass
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        for name, fn in (("github", gh_release_host.upload),
                         ("catbox", _upload_catbox),
                         ("litterbox", _upload_litterbox)):
            try:
                url = fn(file_path)
                verify_public_url(url, expect_bytes=expect_bytes)
                print(f"Hosted via {name}: {url}")
                return url
            except Exception as e:
                print(f"{name} upload failed (try {attempt}/{UPLOAD_ATTEMPTS}): {e}")
                errors.append(f"{name}#{attempt}: {e}")
        if attempt < UPLOAD_ATTEMPTS:
            print(f"  ホスト再試行まで{UPLOAD_BACKOFF_S}秒待機")
            time.sleep(UPLOAD_BACKOFF_S)
    raise RuntimeError("All public hosts failed -> " + " | ".join(errors[-4:]))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python media_host.py <file>")
        sys.exit(1)
    print(upload_to_public_url(sys.argv[1]))
