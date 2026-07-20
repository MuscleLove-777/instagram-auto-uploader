# -*- coding: utf-8 -*-
"""
auto_post_instagram.py — Instagram 無人デイリー投稿（SFW筋トレ動画リール、1実行1本）

TikTok版(auto_post_tiktok.py)と同じ設計:
  専用Chromeプロファイル(.instagram_profile)に人間が1回ログイン → 以降Playwright headlessで
  instagram.com の作成フローを操作してリール投稿。パスワードをスクリプトに渡さない
  （過去のinstagrapi/Graph API路線はログイン/ホスティングで頓挫 → ブラウザ実セッション方式が解）。

★SFWのみ: tiktok-auto-uploader/approved_sfw.json（Claude目視承認済み）を共用。
★1実行=1投稿。1日上限・最小間隔・再投稿間隔ガード。
★captcha/チャレンジ検知で安全中断（Claudeは解かない）。

使い方:
  python auto_post_instagram.py            # 通常実行（ジッター→ガード→投稿）
  python auto_post_instagram.py --now      # ジッター無し（テスト用）
  python auto_post_instagram.py --file <path>
  python auto_post_instagram.py login      # 初回ログイン用の窓を開く

exit code: 0=成功/正常スキップ, 2=要ログイン, 3=captcha/チャレンジ, 1=失敗
環境変数: IG_HEADLESS=0 でブラウザ表示。
"""
import os, sys, json, time, random, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, ".instagram_profile")
POSTED_LOG = os.path.join(HERE, "posted_instagram.json")
HOME_URL = "https://www.instagram.com/"

sys.path.insert(0, HERE)
from pool_loader import as_insights  # noqa: E402

try:
    from variant_bandit import pick as bandit_pick, log_post
except Exception:
    def bandit_pick(kind, options, rng=random):
        return rng.choice(options), ""
    def log_post(platform, record):
        pass


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

# SFW承認プールはTikTok側と共用（目視選別は1回で全媒体に効かせる）
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


def load_approved_entries():
    d = _load_json(APPROVED_LOG, {})
    out = []
    for x in d.get("approved", []):
        if isinstance(x, dict) and x.get("path"):
            out.append({"path": x["path"], "category": x.get("category", "")})
        elif isinstance(x, str):
            out.append({"path": x, "category": ""})
    return out


def pick_video():
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
        chosen = random.choice(fresh)
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
    ins = as_insights("safe_fitness", platform="instagram")
    templates = ins.get("recommended_templates") or ["今日も積み上げ💪 {hashtags}"]
    tags_all = [t for t in (ins.get("recommended_tags") or []) if t and " " not in t]
    ng = [w.lower() for w in (ins.get("avoid_tags") or [])]

    tpl, tpl_key = bandit_pick("instagram_caption", templates)
    category = os.path.basename(os.path.dirname(video_path)).lower()
    cat_tags = CATEGORY_TAGS.get(category, [])
    rng = random.Random()
    pool = [t for t in tags_all if t not in cat_tags]
    picked = cat_tags + rng.sample(pool, min(6, len(pool)))  # IGはタグ多めが効く
    picked = [t for t in picked if not any(n in t.lower() for n in ng)]
    hashtags = " ".join("#" + t for t in dict.fromkeys(picked))

    caption = tpl.replace("{hashtags}", hashtags).strip()
    if "{hashtags}" not in tpl:
        caption = (caption + " " + hashtags).strip()
    low = caption.lower()
    if any(n in low for n in ng):
        caption = "今日も積み上げ💪 " + hashtags
    return caption[:2000], tpl_key


# ============================================================
# Playwright: Instagram 作成フロー
# ============================================================
JS_HAS_CHALLENGE = """
() => {
  const u = location.href;
  if (u.includes('/challenge') || u.includes('captcha')) return true;
  const sel = ['[id*="captcha"]', 'iframe[src*="captcha"]', 'img[src*="captcha"]'];
  return sel.some(s => document.querySelector(s));
}
"""


def _dismiss_dialogs(page):
    """「ログイン情報を保存」「お知らせをオン」等の後で/今はしないダイアログを閉じる。"""
    for _ in range(3):
        clicked = False
        for txt in ["後で", "今はしない", "Not Now", "OK", "閉じる"]:
            try:
                b = page.get_by_role("button", name=txt, exact=True).first
                if b.count() > 0 and b.is_visible():
                    b.click(timeout=2000)
                    clicked = True
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue
        if not clicked:
            break


def is_logged_in(page):
    url = page.url or ""
    if "accounts/login" in url or "/challenge" in url:
        return False
    try:
        # 明確なログアウト証拠（パスワード欄/ログインフォーム）があれば即False
        if page.locator("input[name='password'], input[type='password']").count() > 0:
            return False
        # ログイン済みの強いシグナルのみ採用（甘いnavフォールバックは誤判定するため廃止）
        for label in ["新規投稿", "作成", "検索", "ホーム", "New post", "Create", "Search", "Home"]:
            if page.locator(f"svg[aria-label='{label}']").count() > 0:
                return True
        if page.locator("a[href*='/direct/']").count() > 0:
            return True
        if page.locator("img[alt*='プロフィール写真'], img[alt*='profile picture' i]").count() > 0:
            return True
        return False
    except Exception:
        return False


def _shot(page, name):
    try:
        page.screenshot(path=os.path.join(HERE, name))
        print(f"  screenshot: {name}")
    except Exception:
        pass


def _click_first(page, specs, timeout=4000):
    """specs: (kind, value) kind=role_button|text|css。最初に見つかった可視要素をクリック。"""
    for kind, val in specs:
        try:
            if kind == "role_button":
                loc = page.get_by_role("button", name=val, exact=True).first
            elif kind == "text":
                loc = page.get_by_text(val, exact=True).first
            elif kind == "css":
                loc = page.locator(val).first
            else:
                continue
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=timeout)
                return val
        except Exception:
            continue
    return None


def post_video(page, video_path, caption):
    print("Step: ホーム")
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)

    if not is_logged_in(page):
        print("NEEDS_LOGIN")
        _shot(page, "debug_ig_login.png")
        return "LOGIN"
    if page.evaluate(JS_HAS_CHALLENGE):
        print("NEEDS_CAPTCHA: チャレンジ/captcha検知 → 中断")
        _shot(page, "debug_ig_captcha.png")
        return "CAPTCHA"
    _dismiss_dialogs(page)

    print("Step: 作成メニュー")
    opened = _click_first(page, [
        ("css", "svg[aria-label='新規投稿']"),
        ("css", "svg[aria-label='作成']"),
        ("css", "svg[aria-label='New post']"),
        ("css", "svg[aria-label='Create']"),
        ("text", "作成"),
    ])
    if not opened:
        print("作成ボタンが見つかりません")
        _shot(page, "debug_ig_create.png")
        return "FAIL"
    page.wait_for_timeout(1500)
    # サブメニューに「投稿」がある場合は選ぶ
    _click_first(page, [("text", "投稿"), ("text", "Post")], timeout=2500)
    page.wait_for_timeout(1500)

    print("Step: 動画ファイル選択")
    try:
        fi = page.locator("input[type=file]").last
        fi.set_input_files(video_path, timeout=30000)
    except Exception as e:
        print(f"ファイル選択失敗: {e}")
        _shot(page, "debug_ig_file.png")
        return "FAIL"
    page.wait_for_timeout(4000)

    # 「リール動画として共有されます」等の告知 → OK
    _click_first(page, [("role_button", "OK")], timeout=3000)
    page.wait_for_timeout(1000)

    print("Step: 次へ ×2（トリミング→編集）")
    for i in range(2):
        nxt = None
        for _ in range(15):
            nxt = _click_first(page, [
                ("role_button", "次へ"),
                ("text", "次へ"),
                ("role_button", "Next"),
            ], timeout=3000)
            if nxt:
                break
            page.wait_for_timeout(2000)
        if not nxt:
            print(f"「次へ」({i+1}回目)が見つかりません")
            _shot(page, f"debug_ig_next{i+1}.png")
            return "FAIL"
        page.wait_for_timeout(2500)

    print("Step: キャプション入力")
    try:
        ed = None
        for sel in ["div[aria-label*='キャプション']",
                    "div[aria-label*='caption' i]",
                    "div[contenteditable='true']"]:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                ed = loc
                break
        if ed:
            ed.click()
            page.keyboard.type(caption, delay=25)
        else:
            print("  WARN: キャプション欄が見つかりません（投稿は続行）")
            _shot(page, "debug_ig_caption.png")
    except Exception as e:
        print(f"  WARN: キャプション入力失敗: {e}")
        _shot(page, "debug_ig_caption.png")

    if page.evaluate(JS_HAS_CHALLENGE):
        print("NEEDS_CAPTCHA: 投稿直前にチャレンジ → 中断")
        _shot(page, "debug_ig_captcha.png")
        return "CAPTCHA"

    print("Step: シェア")
    shared = _click_first(page, [
        ("role_button", "シェア"),
        ("text", "シェア"),
        ("role_button", "Share"),
    ], timeout=6000)
    if not shared:
        print("シェアボタンが見つかりません")
        _shot(page, "debug_ig_share.png")
        return "FAIL"

    print("Step: 完了確認")
    ok = False
    for _ in range(20):  # リールは処理に時間がかかる（最大60秒）
        page.wait_for_timeout(3000)
        for kw in ["リールをシェアしました", "投稿がシェアされました", "シェアされました",
                   "Your reel has been shared", "has been shared"]:
            try:
                if page.get_by_text(kw, exact=False).count() > 0:
                    ok = True
                    break
            except Exception:
                continue
        if ok:
            break
    if ok:
        print("  投稿成功")
        return "OK"
    _shot(page, "debug_ig_result.png")
    print("  投稿結果を確認できず（スクショ保存）。手動確認要")
    return "FAIL"


# ============================================================
# 実行制御
# ============================================================
def _post(video_path, category=""):
    caption, tpl_key = build_caption(video_path)
    print(f"動画   : {os.path.basename(video_path)}")
    print(f"caption: {caption}")

    from playwright.sync_api import sync_playwright
    headless = os.environ.get("IG_HEADLESS", "1") != "0"
    status = "FAIL"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=headless,
            viewport={"width": 1380, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            status = post_video(page, video_path, caption)
        finally:
            ctx.close()

    if status == "OK":
        log = load_posted()
        log["files"].append({
            "file": os.path.basename(video_path),
            "abspath": video_path,
            "category": category,
            "caption": caption,
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_posted(log)
        try:
            log_post("instagram", {"file": os.path.basename(video_path),
                                   "variant": tpl_key, "caption": caption})
        except Exception:
            pass
        print(f"完了。累計投稿: {len(log['files'])}")
        return 0
    if status == "LOGIN":
        return 2
    if status == "CAPTCHA":
        return 3
    return 1


def run(only_file=None, now=False):
    if not os.path.isdir(PROFILE_DIR):
        print("NEEDS_LOGIN: 専用プロファイル未作成。`auto_post_instagram.py login` でログインしてください。")
        return 2

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
    max_per_day = int(os.environ.get("IG_MAX_PER_DAY", "2"))
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

    chosen = pick_video()
    if not chosen:
        return 0
    return _post(chosen["path"], chosen.get("category", ""))


def run_login():
    """初回ログイン用: 専用プロファイルで窓を開く。人間がログイン（FB連携/2FAもここで）。"""
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            viewport={"width": 1280, "height": 860},
            chromium_sandbox=True,   # --no-sandbox警告を消す（FBログインの白画面対策）
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        print("ログインしてください（FBログイン/2FA/captchaもこの窓で）。完了を検知したら自動で閉じます。")
        ok = False
        for _ in range(900):  # 最大30分
            time.sleep(2)
            try:
                if not ctx.pages:
                    break
                pg = ctx.pages[0]
                url = pg.url or ""
                # 操作を邪魔しない: ページ遷移はさせず、今の画面を受動的に判定するだけ
                if ("instagram.com" in url and "accounts/login" not in url
                        and "/challenge" not in url and is_logged_in(pg)):
                    ok = True
                    print("ログイン検知。セッションを保存して閉じます…")
                    time.sleep(3)   # cookie書き込み猶予
                    break
            except Exception:
                continue
        try:
            ctx.close()
        except Exception:
            pass
    print("LOGIN_OK: プロファイル保存完了。以降は無人投稿できます。" if ok
          else "LOGIN_UNKNOWN: ログイン未確認。もう一度 login を実行してください。")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "login":
        sys.exit(run_login())
    only = None
    now = "--now" in args
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args):
            only = args[i + 1]
    sys.exit(run(only_file=only, now=now))


if __name__ == "__main__":
    main()
