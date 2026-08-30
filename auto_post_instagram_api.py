# -*- coding: utf-8 -*-
"""
auto_post_instagram_api.py — Instagram 無人デイリー投稿（公式Graph API・リール1本/実行）

Facebook版(facebook-auto-uploader/auto_post_facebook.py)と同じ設計思想:
  - 素材: tiktok-auto-uploader/approved_sfw.json（Claude目視SFW承認プール）を共用
  - 投稿: Instagram Graph API（プロアカウント×連携FBページの無期限ページトークン）
  - ガード: 1日上限/最小間隔/再投稿間隔/ジッター

★Facebookとの決定的な違い: Instagramはローカルバイナリを直接送れない。
  公開URL(video_url)を渡してコンテナを作る仕様なので、media_host.py で
  匿名ホスト(litterbox 72h)へ上げてから渡す。

★キャプションにURLは入れない: IGは本文のURLがクリックできず、外部誘導は
  リーチを削るだけになる。導線はプロフィールのリンクに寄せる。

必要な .env（このフォルダ、Git管理外）:
  IG_USER_ID=<Instagramビジネスアカウントの数値ID>
  IG_ACCESS_TOKEN=<連携FBページの無期限トークン>

使い方:
  python auto_post_instagram_api.py          # 通常（ジッター→ガード→投稿）
  python auto_post_instagram_api.py --now    # ジッター無し（テスト）
  python auto_post_instagram_api.py --file <path>
  python auto_post_instagram_api.py check    # 疎通確認（投稿しない）

exit: 0=成功/正常スキップ, 2=トークン未設定/失効, 1=失敗
"""
import os, sys, json, time, random, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
POSTED_LOG = os.path.join(HERE, "posted_instagram_api.json")
GRAPH = "https://graph.facebook.com/v21.0"

sys.path.insert(0, HERE)
from pool_loader import as_insights          # noqa: E402
from media_host import upload_to_public_url, cleanup_public_url  # noqa: E402


def _load_dotenv(path=None):
    if path is None:
        path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_load_dotenv()

APPROVED_LOG = os.path.abspath(os.path.join(
    HERE, os.environ.get("APPROVED_SFW_PATH",
                         r"..\tiktok-auto-uploader\approved_sfw.json")))


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            print(f"WARNING: {os.path.basename(path)} を読めませんでした")
    return default


def load_posted():
    return _load_json(POSTED_LOG, {"files": []})


def save_posted(log):
    with open(POSTED_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _approval_fingerprint_matches(entry):
    """Only the exact reviewed bytes inherit a snapshot approval."""
    expected = entry.get("sha256")
    if not expected:
        return True
    import hashlib
    try:
        digest = hashlib.sha256()
        with open(entry["path"], "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    except OSError:
        return False


def load_approved_entries():
    d = _load_json(APPROVED_LOG, {})
    out = []
    for x in d.get("approved", []):
        if isinstance(x, dict) and x.get("path"):
            if not _approval_fingerprint_matches(x):
                continue
            out.append({"path": x["path"], "category": x.get("category", "")})
        elif isinstance(x, str):
            out.append({"path": x, "category": ""})
    return out


def _cat(m):
    """カテゴリ名。未設定なら置き場のフォルダ名で代用（履歴と候補で表記を揃える）。"""
    path = m.get("path") or m.get("abspath") or ""
    return (m.get("category") or os.path.basename(os.path.dirname(path)) or "")


def _balanced_choice(cands, posted, lookback=4):
    """直近に出したカテゴリほど選ばれにくくする。
    一様ランダムだと「懸垂」が偏って続くため（実績: 直近4本中3本が懸垂）。"""
    recent = [_cat(e) for e in posted.get("files", [])[-lookback:]]
    groups = {}
    for m in cands:
        groups.setdefault(_cat(m), []).append(m)
    if len(groups) < 2:
        return random.choice(cands)
    cats = list(groups)
    weights = [0.25 ** recent.count(c) for c in cats]      # 直近1回ごとに1/4
    cat = random.choices(cats, weights=weights)[0]
    print(f"  カテゴリ配分: 直近{recent} → {cat or '(なし)'}")
    return random.choice(groups[cat])


def pick_video(exclude=None):
    exclude = {_norm(p) for p in (exclude or [])}
    metas = load_approved_entries()
    if not metas:
        print(f"承認プールが空です（{APPROVED_LOG}）→ 投稿スキップ")
        return None
    posted = load_posted()
    last_at = {}
    for e in posted.get("files", []):
        if isinstance(e, dict) and e.get("abspath"):
            n = _norm(e["abspath"])
            at = str(e.get("uploaded_at", ""))
            if n not in last_at or at > last_at[n]:
                last_at[n] = at
    fresh, reusable = [], []
    min_repost_days = int(os.environ.get("IG_MIN_REPOST_DAYS", "14"))
    now = datetime.datetime.now()
    for m in metas:
        if not os.path.exists(m["path"]):
            continue
        n = _norm(m["path"])
        if n in exclude:            # 同一実行内で失敗済み → 別素材で再挑戦
            continue
        if n not in last_at:
            fresh.append(m)
            continue
        try:
            dt = datetime.datetime.strptime(last_at[n], "%Y-%m-%d %H:%M:%S")
            if (now - dt).days >= min_repost_days:
                reusable.append(dict(m, last=last_at[n]))
        except Exception:
            pass
    if fresh:
        chosen = _balanced_choice(fresh, posted)
        print(f"選択(未投稿 {len(fresh)}/{len(metas)}): {os.path.basename(chosen['path'])}")
        return chosen
    if reusable:
        reusable.sort(key=lambda m: m.get("last", ""))
        chosen = reusable[0]
        print(f"全て投稿済み → 最古を再利用: {os.path.basename(chosen['path'])} (last={chosen.get('last')})")
        return chosen
    print(f"承認プール{len(metas)}本は全て{min_repost_days}日以内に投稿済み → スキップ")
    return None


CATEGORY_TAGS = {
    "pullups": ["懸垂", "calisthenics"],
    "training": ["筋トレ", "workout"],
}


def build_caption(video_path):
    """IGはキャプションのURLが機能しない → 誘導はプロフィールリンクへ寄せる。
    タグはIGの実用上限30個より控えめに10個弱。"""
    ins = as_insights("safe_fitness", platform="instagram")
    templates = ins.get("recommended_templates") or ["今日も積み上げ💪 {hashtags}"]
    tags_all = [t for t in (ins.get("recommended_tags") or []) if t and " " not in t]
    ng = [w.lower() for w in (ins.get("avoid_tags") or [])]

    # 直近の投稿と同じ文面・同じタグ並びを避ける（同じ煽り文が3回出ていたため）
    recent = [str(e.get("caption", "")) for e in load_posted().get("files", [])[-5:]]
    used_bodies = {r.split("\n")[0].strip() for r in recent}
    unused = [t for t in templates
              if t.replace("{hashtags}", "").strip() not in used_bodies]
    tpl = random.choice(unused or templates)

    prev_tags = set()
    for r in recent[-2:]:
        prev_tags |= {w.lstrip("#") for w in r.split() if w.startswith("#")}

    category = os.path.basename(os.path.dirname(video_path)).lower()
    cat_tags = CATEGORY_TAGS.get(category, [])
    if cat_tags and prev_tags.issuperset(cat_tags):
        cat_tags = random.sample(cat_tags, 1)      # 連続時はカテゴリタグも間引く
    pool = [t for t in tags_all if t not in cat_tags]
    fresh_pool = [t for t in pool if t not in prev_tags]
    need = 7 - len(fresh_pool)
    if need > 0:                                   # 足りない分だけ既出から補充
        fresh_pool += random.sample([t for t in pool if t in prev_tags],
                                    min(need, len(pool) - len(fresh_pool)))
    picked = cat_tags + random.sample(fresh_pool, min(7, len(fresh_pool)))
    random.shuffle(picked)
    picked = [t for t in picked if not any(n in t.lower() for n in ng)]
    hashtags = " ".join("#" + t for t in dict.fromkeys(picked))

    body = tpl.replace("{hashtags}", "").strip()
    caption = "\n\n".join(x for x in [body, "リンクはプロフィールから", hashtags] if x)
    low = caption.lower()
    if any(n in low for n in ng):
        caption = "今日も積み上げ💪\n\n" + hashtags
    if "http" in caption:      # 保険: URLは絶対に入れない
        caption = "\n\n".join(p for p in caption.split("\n\n") if "http" not in p)
    return caption[:2200]


def _cred():
    return (os.environ.get("IG_USER_ID", "").strip(),
            os.environ.get("IG_ACCESS_TOKEN", "").strip())


def check():
    ig_id, token = _cred()
    if not ig_id or not token:
        print("NEEDS_TOKEN: .env に IG_USER_ID / IG_ACCESS_TOKEN を設定してください")
        return 2
    try:
        d = requests.get(f"{GRAPH}/{ig_id}",
                         params={"fields": "username,followers_count,media_count",
                                 "access_token": token}, timeout=30).json()
    except Exception as e:
        print(f"疎通失敗: {e}")
        return 1
    if "error" in d:
        print(f"NEEDS_TOKEN: APIエラー: {d['error'].get('message')}")
        return 2
    print(f"OK: @{d.get('username')} (followers={d.get('followers_count')}, media={d.get('media_count')})")
    return 0


def post_reel(video_path, caption):
    """公開URL化 → REELSコンテナ生成 → 処理完了待ち → publish。"""
    ig_id, token = _cred()
    if not ig_id or not token:
        print("NEEDS_TOKEN: .env に IG_USER_ID / IG_ACCESS_TOKEN を設定してください")
        return "TOKEN"
    size_mb = os.path.getsize(video_path) / 1e6
    print(f"Step1: 公開URL化（{size_mb:.1f}MB）")
    try:
        video_url = upload_to_public_url(video_path)
    except Exception as e:
        print(f"公開URL化に失敗: {e}")
        return "FAIL"

    def _abort(status="FAIL"):
        cleanup_public_url(video_url)        # 失敗時も一時ファイルを残さない
        return status

    print("Step2: コンテナ生成（REELS）")
    d = requests.post(f"{GRAPH}/{ig_id}/media", timeout=180, data={
        "media_type": "REELS", "video_url": video_url,
        "caption": caption, "access_token": token,
    }).json()
    if "error" in d:
        err = d["error"]
        print(f"APIエラー: code={err.get('code')} {err.get('message')}")
        return _abort("TOKEN" if err.get("code") in (190, 102, 104) else "FAIL")
    cid = d.get("id")

    print("Step3: 処理待ち")
    for i in range(40):                      # 最大約10分
        time.sleep(15)
        s = requests.get(f"{GRAPH}/{cid}",
                         params={"fields": "status_code,status", "access_token": token},
                         timeout=60).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            print(f"  変換に失敗: {s.get('status')}")
            return _abort()
        print(f"  ...{code} ({(i + 1) * 15}s)")
    else:
        print("  タイムアウト（処理が終わらない）")
        return _abort()

    print("Step4: 公開")
    p = requests.post(f"{GRAPH}/{ig_id}/media_publish", timeout=120,
                      data={"creation_id": cid, "access_token": token}).json()
    if "error" in p:
        print(f"公開に失敗: {p['error'].get('message')}")
        return _abort()
    print(f"  投稿成功: media_id={p.get('id')}")
    cleanup_public_url(video_url)          # 一時ファイルを残さない
    return "OK:" + str(p.get("id"))


def _post(video_path, category=""):
    caption = build_caption(video_path)
    print(f"動画   : {os.path.basename(video_path)}")
    print(f"caption: {caption.replace(chr(10), ' / ')}")
    status = post_reel(video_path, caption)
    if status.startswith("OK:"):
        log = load_posted()
        log["files"].append({
            "file": os.path.basename(video_path),
            "abspath": video_path,
            "category": category,
            "media_id": status[3:],
            "caption": caption,
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_posted(log)
        print(f"完了。累計投稿: {len(log['files'])}")
        return 0
    if status == "TOKEN":
        return 2
    return 1


def run(only_file=None, now=False):
    if only_file:
        if not os.path.exists(only_file):
            print(f"ファイルがありません: {only_file}")
            return 1
        return _post(only_file)

    log = load_posted()
    posts = [str(e.get("uploaded_at", "")) for e in log.get("files", [])
             if isinstance(e, dict) and e.get("uploaded_at")]
    today = time.strftime("%Y-%m-%d")
    today_n = sum(1 for a in posts if a.startswith(today))
    max_per_day = int(os.environ.get("IG_MAX_PER_DAY", "1"))
    if today_n >= max_per_day:
        print(f"本日は既に{today_n}件投稿済み（上限{max_per_day}）→ スキップ")
        return 0
    min_gap = int(os.environ.get("IG_MIN_GAP_MIN", "300"))
    if posts:
        try:
            last_dt = datetime.datetime.strptime(max(posts), "%Y-%m-%d %H:%M:%S")
            gap = (datetime.datetime.now() - last_dt).total_seconds() / 60
            if gap < min_gap:
                print(f"直近投稿から{gap:.0f}分（最小{min_gap}分）→ スキップ")
                return 0
        except Exception:
            pass

    jitter = int(os.environ.get("IG_JITTER_MIN", "12"))
    if not now and jitter > 0:
        wait_s = random.randint(0, jitter * 60)
        print(f"ジッター待機 {wait_s // 60}分{wait_s % 60}秒")
        time.sleep(wait_s)

    # IG側の取得/変換は体感5割で落ちる（error code 0 / 2207082）。
    # 1回失敗＝その日の枠を落とす作りだったので、素材を替えて数回粘る。
    attempts = int(os.environ.get("IG_POST_ATTEMPTS", "3"))
    retry_wait_s = int(os.environ.get("IG_RETRY_WAIT_S", "90"))
    tried, rc = [], 1
    for i in range(1, attempts + 1):
        chosen = pick_video(exclude=tried)
        if not chosen:
            return 0 if i == 1 else rc
        tried.append(chosen["path"])
        rc = _post(chosen["path"], _cat(chosen))
        if rc in (0, 2):                     # 成功 / トークン切れ（粘っても無駄）
            return rc
        if i < attempts:
            print(f"失敗 {i}/{attempts} → {retry_wait_s}秒後に別素材で再挑戦")
            time.sleep(retry_wait_s)
    print(f"{attempts}回とも失敗 → 今回は投稿なし")
    return rc


def main():
    args = sys.argv[1:]
    if args and args[0] == "check":
        sys.exit(check())
    only = None
    now = "--now" in args
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args):
            only = args[i + 1]
    sys.exit(run(only_file=only, now=now))


if __name__ == "__main__":
    main()
