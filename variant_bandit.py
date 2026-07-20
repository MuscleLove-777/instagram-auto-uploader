# -*- coding: utf-8 -*-
"""
M国 変種バンディット（全uploader共通モジュール・ドロップイン可能）

「どのキャプション/タイトル/CTAが実際に反応(流入・notes・fav)を取ったか」を
投稿単位で学習するための最小部品。3点セットで閉ループになる:

  1) pick(kind, options)      … content_pool.json の variant_weights による重み付き抽選
                                （25%は一様探索 = 新変種が絶対に死なない）
  2) with_utm_content(url, k) … 発リンクへ utm_content=<variant_key> を付与
                                （GA4側で変種単位の流入計測が可能になる）
  3) log_post(platform, rec)  … posted_log.json へ変種付き投稿記録を追記
                                （autonomy/analyze_variants.py が集計→重み再計算）

重みは dashboard/autonomy が毎日 content_pool.json に埋めて配布する。
重みが無い間は一様ランダム（従来挙動と同一）＝絶対に死なない（憲法第1条）。
正本: dashboard/autonomy/variant_bandit.py（各uploaderリポへ同一コピーを配置）
"""
import hashlib
import json
import random
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
POOL_PATH = BASE / "content_pool.json"
LOG_PATH = BASE / "posted_log.json"
EXPLORE_RATE = 0.25   # 探索率: この確率で重みを無視して一様抽選する
MIN_WEIGHT = 0.05
MAX_LOG_POSTS = 500


def variant_id(text) -> str:
    """変種本文から安定ID(8桁hex)。テンプレ文言が変われば別IDになり自動で新変種扱い。"""
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]


def _load_weights(kind: str) -> dict:
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        vw = data.get("variant_weights") or {}
        m = vw.get(kind) or {}
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def pick(kind: str, options, rng=random):
    """options から1つ重み付き抽選して (選択肢, variant_id) を返す。
    kind例: "tumblr.caption" / "deviantart.title" / "rakuten.title" / "rakuten.body"
    """
    opts = [o for o in (options or []) if o is not None]
    if not opts:
        return None, ""
    ids = [variant_id(o) for o in opts]
    weights = _load_weights(kind)
    try:
        if weights and rng.random() >= EXPLORE_RATE:
            ws = [max(float(weights.get(v, 1.0)), MIN_WEIGHT) for v in ids]
            i = rng.choices(range(len(opts)), weights=ws, k=1)[0]
        else:
            i = rng.randrange(len(opts))
    except Exception:
        i = rng.randrange(len(opts))
    return opts[i], ids[i]


def with_utm_content(url: str, variant_key: str) -> str:
    """URLへ utm_content=<variant_key> を付与（既にある場合は触らない）。"""
    if not url or not variant_key or "utm_content=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_content={variant_key}"


def log_post(platform: str, record: dict) -> None:
    """posted_log.json へ追記。ログ失敗で投稿処理は絶対に止めない。"""
    try:
        data = {"posts": []}
        if LOG_PATH.exists():
            loaded = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = {"posts": loaded}
            elif isinstance(loaded, dict):
                data = loaded
        rec = dict(record or {})
        rec.setdefault("platform", platform)
        rec.setdefault("posted_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        posts = data.setdefault("posts", [])
        posts.append(rec)
        if len(posts) > MAX_LOG_POSTS:
            data["posts"] = posts[-MAX_LOG_POSTS:]
        LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[variant_bandit] log_post skipped: {e}")
