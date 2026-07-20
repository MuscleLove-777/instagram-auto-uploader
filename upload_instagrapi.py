# -*- coding: utf-8 -*-
"""Instagram 自動投稿（instagrapi・Tumblr形式踏襲）。

- ログイン済みセッションを再利用して1回1投稿（無人）。login_instagrapi.get_logged_in_client()。
- content_pool の safe_fitness レーン（IG安全側）で本文/タグ/CTAを生成。IG地雷ワードは機械除去。
- 露出ゲート（safety_gate_instagram: 水着OK・乳首/性器/尻の露出は弾く）を通過した素材だけ投稿。
- 変種バンディットで学習、uploaded_instagram.json で重複回避。
- 画像=photo_upload、動画=clip_upload(Reels)。
- 素材は IG_LOCAL_MEDIA_DIR（ローカル）優先、無ければ IG_GDRIVE_FOLDER_ID から gdown、無ければ media_ig/。

使い方:
  python upload_instagrapi.py            # 1投稿
  python upload_instagrapi.py --dry-run  # ログイン/投稿せず、素材選定・ゲート・本文生成だけ確認
"""
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 絵文字入りキャプションをどのコンソール(cp932等)でもログ出力できるように
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 変種バンディット（無くても一様ランダムで動く）
try:
    from variant_bandit import pick as bandit_pick, with_utm_content, log_post
except Exception:
    def bandit_pick(kind, options, rng=random):
        o = rng.choice(options)
        return o, ""

    def with_utm_content(url, key):
        return url

    def log_post(platform, record):
        pass

# 露出ゲート（読めなければ fail-closed で投稿しない）
try:
    from safety_gate_instagram import check as safety_check
    _GATE_ERR = None
except Exception as _e:  # pragma: no cover
    safety_check = None
    _GATE_ERR = _e

LANE = "safe_fitness"          # IGは content_pool 上 safe_fitness 割当（本文/タグはIG安全側で作る）
PLATFORM = "instagram"
GDRIVE_FOLDER_ID = os.environ.get("IG_GDRIVE_FOLDER_ID", "")
LOCAL_MEDIA_DIR = os.environ.get("IG_LOCAL_MEDIA_DIR", "")
UPLOADED_LOG = HERE / "uploaded_instagram.json"
CONTENT_POOL = HERE / "content_pool.json"
MEDIA_DIR = HERE / "media_ig"

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov"}
MAX_IMG = 12 * 1024 * 1024
MAX_VID = 100 * 1024 * 1024
MAX_HASHTAGS = 20
GATE_RETRIES = 8              # ゲート落ち時に別素材を試す上限

# IGで避けたい語（本文/タグから機械除去・シャドウバン/BAN対策）
IG_HOSTILE = {
    "nsfw", "nude", "naked", "porn", "xxx", "hentai", "ero", "erotic", "adult",
    "18+", "18禁", "sex", "sexy", "fetish", "uncensored", "無修正", "エロ", "アダルト", "裸",
}

# content_pool が読めない時のフォールバック
FALLBACK_TAGS = [
    "筋トレ女子", "筋肉女子", "フィットネス", "ワークアウト", "ボディメイク",
    "musclegirl", "fitnessmotivation", "gymgirl", "strongwomen", "workoutmotivation",
    "fitfam", "筋トレ", "フィットネス女子", "gymlife", "AIアート",
]
FALLBACK_CAPS = [
    "今日の一枚、強い。{tags}", "Strong is beautiful💪 {tags}",
    "継続は力なり。今日も積み上げ💪 {tags}", "バキバキ。でも美しい。{tags}",
]
FALLBACK_CTA = ["フォローで毎日筋肉美が届く💪"]


def gather_media():
    """素材一覧を返す。ローカルdir優先→Drive→media_ig/。"""
    src_dirs = []
    if LOCAL_MEDIA_DIR and Path(LOCAL_MEDIA_DIR).is_dir():
        src_dirs.append(Path(LOCAL_MEDIA_DIR))
    if GDRIVE_FOLDER_ID:
        MEDIA_DIR.mkdir(exist_ok=True)
        try:
            import gdown
            gdown.download_folder(
                f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}",
                output=str(MEDIA_DIR), quiet=True,
            )
        except Exception as e:
            print(f"gdown skipped: {e}")
        src_dirs.append(MEDIA_DIR)
    if not src_dirs and MEDIA_DIR.is_dir():
        src_dirs.append(MEDIA_DIR)

    files, seen = [], set()
    for d in src_dirs:
        for root, _, names in os.walk(d):
            for n in names:
                p = Path(root) / n
                if str(p) in seen:
                    continue
                ext = p.suffix.lower()
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if (ext in IMAGE_EXT and size <= MAX_IMG) or (ext in VIDEO_EXT and size <= MAX_VID):
                    files.append(p)
                    seen.add(str(p))
    return files


def load_posted():
    try:
        data = json.loads(UPLOADED_LOG.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def record_posted(name):
    posted = load_posted()
    posted.add(name)
    UPLOADED_LOG.write_text(json.dumps(sorted(posted), ensure_ascii=False, indent=1), encoding="utf-8")


def _scrub(words):
    out = []
    for w in words:
        if not any(bad in str(w).lower() for bad in IG_HOSTILE):
            out.append(w)
    return out


def _insights():
    try:
        from pool_loader import as_insights
        return as_insights(LANE, platform=PLATFORM) or {}
    except Exception as e:
        print(f"pool_loader skipped: {e}")
        return {}


def build_tags(ins):
    tags = list(ins.get("recommended_tags") or FALLBACK_TAGS)
    avoid = {a.lower() for a in ins.get("avoid_tags", [])}
    if avoid:
        tags = [t for t in tags if t.lower() not in avoid]
    tags = _scrub(tags)
    seen, uniq = set(), []
    for t in tags:
        k = str(t).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq[:MAX_HASHTAGS]


def build_caption(ins, tags):
    templates = _scrub(ins.get("recommended_templates") or FALLBACK_CAPS) or FALLBACK_CAPS
    ctas = _scrub(ins.get("recommended_ctas") or FALLBACK_CTA) or FALLBACK_CTA
    hashtags = " ".join(f"#{str(t).replace(' ', '').replace('　', '')}" for t in tags)

    template, cap_vid = bandit_pick("instagram.caption", templates)
    variant_key = f"cap{cap_vid}" if cap_vid else ""
    cta, cta_vid = bandit_pick("instagram.cta", ctas)
    cta = with_utm_content(cta, variant_key)

    body = str(template).replace("{tags}", "").replace("{hashtags}", "").strip()
    caption = f"{body}\n\n{cta}\n\n{hashtags}".strip()
    return caption, cap_vid, cta_vid


def load_ng_globals():
    try:
        data = json.loads(CONTENT_POOL.read_text(encoding="utf-8"))
        ng = [str(w) for w in data.get("ng_words_global", []) if str(w).strip()]
        if ng:
            return ng
    except Exception:
        pass
    return ["アツロウ", "あつろう", "atsuro", "atsurou", "アツロー", "GOOGLE_API_KEY"]


def caption_is_safe(caption):
    low = caption.lower()
    for ng in load_ng_globals():
        if ng.lower() in low:
            return False, ng
    return True, ""


def pick_gated_media(dry_run=False):
    """ゲートを通る未投稿素材を1つ選ぶ。無ければ None。"""
    media = gather_media()
    if not media:
        print("No media found（IG_LOCAL_MEDIA_DIR / IG_GDRIVE_FOLDER_ID / media_ig/ に素材を置いてください）")
        return None, media
    posted = load_posted()
    candidates = [m for m in media if m.name not in posted]
    random.shuffle(candidates)
    if not candidates:
        print("全素材が投稿済み。新規素材を追加してください。")
        return None, media
    for m in candidates[:GATE_RETRIES]:
        ok, reason = safety_check(str(m))
        print(f"gate {'PASS' if ok else 'BLOCK'}: {m.name} ({reason})")
        if ok:
            return m, media
    print("ゲートを通る素材がありませんでした（今回は投稿なし）")
    return None, media


def main():
    dry_run = "--dry-run" in sys.argv
    if safety_check is None:
        print(f"ABORT: safety_gate_instagram を読み込めません: {_GATE_ERR}")
        return 1

    chosen, _all = pick_gated_media(dry_run=dry_run)

    # 本文はゲート結果と独立に組める（dry-runでも中身を確認したい）
    ins = _insights()
    tags = build_tags(ins)
    caption, cap_vid, cta_vid = build_caption(ins, tags)
    safe, ng = caption_is_safe(caption)
    if not safe:
        print(f"ABORT: キャプションに禁止語 '{ng}'（固有名詞ガード・憲法第4条）")
        return 1

    if dry_run:
        print("----- DRY RUN -----")
        print(f"chosen: {chosen.name if chosen else '(なし)'}")
        print(f"caption_variant={cap_vid or '(uniform)'}  cta_variant={cta_vid or '(uniform)'}")
        print("----- caption -----")
        print(caption)
        return 0

    if chosen is None:
        return 0

    from login_instagrapi import get_logged_in_client
    try:
        cl, mode = get_logged_in_client()
    except Exception as e:
        print(f"LOGIN_FAILED: {type(e).__name__}: {e}")
        return 1
    print(f"login ok ({mode})")

    ext = chosen.suffix.lower()
    print(f"posting: {chosen.name}  caption_variant={cap_vid or '(uniform)'}")
    try:
        if ext in VIDEO_EXT:
            media_obj = cl.clip_upload(str(chosen), caption)   # Reels
        else:
            media_obj = cl.photo_upload(str(chosen), caption)
    except Exception as e:
        print(f"UPLOAD_FAILED: {type(e).__name__}: {e}")
        return 1

    code = getattr(media_obj, "code", "") or getattr(media_obj, "pk", "")
    print(f"Success! https://www.instagram.com/p/{code}/")
    record_posted(chosen.name)
    log_post(PLATFORM, {
        "media_code": str(code),
        "file": chosen.name,
        "type": "reel" if ext in VIDEO_EXT else "photo",
        "variants": {"instagram.caption": cap_vid, "instagram.cta": cta_vid},
        "tags_count": len(tags),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
