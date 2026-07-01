# -*- coding: utf-8 -*-
"""
ローカルファイル → 公開URL 変換モジュール（鍵不要・憲法第4条準拠）

Instagram Graph API はローカルバイナリを受け取れず、
公開URL(image_url / video_url)を指定してコンテナを作る仕様。
Google Drive の uc?export=download URL は Instagram 側が取得に失敗する
（HTMLインターステイシャルが返る）ため、匿名アップロード先で
「直リンクの公開URL」を作ってから Instagram に渡す。

優先順:
  ① litterbox.catbox.moe（一時ホスト・72時間で自動消滅＝ゴミを残さない）
  ② catbox.moe（恒久ホスト・フォールバック）

どちらも APIキー不要。プレーンテキストで公開URLを返す。
Instagram は container 生成時に即取得するので 72h 保持で十分。
"""
import os
import requests

LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
CATBOX_API = "https://catbox.moe/user/api.php"
HTTP_TIMEOUT = 180


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


def upload_to_public_url(file_path):
    """ローカルファイルを匿名ホストへ上げて公開URLを返す。両方失敗時は例外。"""
    errors = []
    for name, fn in (("litterbox", _upload_litterbox), ("catbox", _upload_catbox)):
        try:
            url = fn(file_path)
            print(f"Hosted via {name}: {url}")
            return url
        except Exception as e:
            print(f"{name} upload failed: {e}")
            errors.append(f"{name}: {e}")
    raise RuntimeError("All public hosts failed -> " + " | ".join(errors))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python media_host.py <file>")
        sys.exit(1)
    print(upload_to_public_url(sys.argv[1]))
