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


def cadence_factor(platform: str) -> float:
    """この媒体の投稿頻度係数を content_pool.json から読む（チャネル間バンディット）。
    1.0=現状維持 / 1.25=勝ち媒体で増枠 / 0.5=流入ゼロ30日で半減 / 0.25=60日 / 0=停止中。
    プールが無い・キーが無い場合は 1.0（従来挙動と同一＝絶対に死なない）。"""
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        cw = (data.get("channel_weights") or {}).get(platform) or {}
        v = float(cw.get("cadence_factor", 1.0))
        return v if 0.0 <= v <= 2.0 else 1.0
    except Exception:
        return 1.0


def posts_this_run(platform: str, rng=random) -> int:
    """この実行で何本投稿するかを返す（0/1/2）。cadence_factor を期待値どおりに実現する。
      1.25 → 1本、25%の確率で2本（＝平均1.25本＝勝ち媒体の増枠）
      1.0  → 1本（現状維持）
      0.5  → 50%の確率で1本（＝流入ゼロ30日の半減）
      0.0  → 0本（停止中媒体）
    uploader側は `for _ in range(variant_bandit.posts_this_run("tumblr")):` で投稿を回す。
    プールが無ければ 1 を返す＝従来挙動（絶対に死なない）。"""
    f = cadence_factor(platform)
    n = int(f)
    frac = f - n
    try:
        if frac > 0 and rng.random() < frac:
            n += 1
    except Exception:
        return 1
    if n < 1:
        # 無言スキップは「タスク緑・投稿ゼロ」に見えて監視が誤検知するため必ず記録する
        log_post(platform, {"skipped": "cadence", "factor": round(f, 3), "posted": False})
    return n


def should_post_now(platform: str, rng=random) -> bool:
    """cadence_factor に従って「今回の投稿枠を実行するか」を確率的に決める。
    uploaderの投稿直前に1行入れるだけで頻度調整が効く:
        if not variant_bandit.should_post_now("tumblr"): return
    係数1.0以上は常にTrue（枠を増やすのはスケジュール側の仕事）。0なら常にFalse。"""
    f = cadence_factor(platform)
    if f >= 1.0:
        return True
    ok = False if f <= 0.0 else _rand_ok(rng, f)
    if not ok:
        # 無言でスキップすると「タスク緑・投稿ゼロ」になり監視が誤検知する（既知事象）。
        # 必ず理由を残す。
        log_post(platform, {"skipped": "cadence", "factor": round(f, 3), "posted": False})
    return ok


def _rand_ok(rng, f):
    try:
        return rng.random() < f
    except Exception:
        return True


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
