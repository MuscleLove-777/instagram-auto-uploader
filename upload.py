# -*- coding: utf-8 -*-
"""
Instagram 自動投稿（GitHub Actions用）
Google Driveから画像/動画取得 → ランダム1つ → 公開URL化 → Instagram Graph APIで投稿

Instagram Graph API（公式 / Business・Creatorアカウント + Facebookページ連携）使用。

★Facebookとの決定的な違い（ここが本ツールの肝）★
  Facebookはローカルファイルを直接multipartアップロードできるが、
  Instagramは「公開URL(image_url/video_url)を指定 → コンテナ生成 → publish」の
  2段階フローしか無く、ローカルバイナリの直接アップロードは不可。
  さらに Google Drive の uc?export=download URL は Instagram 側が取得に失敗する。
  そこで download → 匿名ホストで公開URL化(media_host) → コンテナ → publish とする。
  （旧実装は GOOGLE_API_KEY 前提で憲法違反かつ実際に投稿できなかったため作り直し）

M国憲法「何もしなくても動く完全自動運営」準拠:
  失敗隔離 / 冪等(uploadedログ) / 鍵不要(gdown+匿名ホスト) / pool自動最適化フォールバック /
  発信物に固有名詞を出さない(NGワード二重ガード, 第4条) / Instagram安全側(露骨NSFW除外)。
制限: Instagram Content Publishing は 24時間あたり 50投稿まで。画像はJPEGのみ受理。
"""
import sys
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta
import requests

JST = timezone(timedelta(hours=9))

# --- 環境変数 ---
IG_USER_ID = os.environ.get("IG_USER_ID", "")            # Instagram Business Account ID（数値ID）
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")  # 連携FBページの長期アクセストークン
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID_INSTAGRAM", "")
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

PATREON_LINK = "https://www.patreon.com/c/MuscleLove?utm_source=instagram&utm_medium=autopost"
HUB_LINK = "https://musclelove-777.github.io/?utm_source=instagram&utm_medium=autopost"

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov'}  # InstagramはREELSでmp4/mov対応（GIFは不可）
UPLOADED_LOG = "uploaded_instagram.json"

# --- NGワード（絶対に投稿しない: 発信物に固有名詞を出さない 憲法第4条） ---
NG_WORDS = ['アツロウ', 'あつろう', 'atsuro', 'Atsuro', 'ATSURO']

# --- 露骨NSFW検出（Instagramはヌード/性的表現BAN。ファイル名で疑わしきは投稿しない） ---
NSFW_KEYWORDS = ['nsfw', 'nude', 'naked', 'erotic', 'xxx', 'porn', ' sex', 'エロ', '裸', 'ヌード']

# --- タグマッピング（ファイル名 → 関連ハッシュタグ） ---
CONTENT_TAG_MAP = {
    'training': ['筋トレ', 'workout', 'training', 'gym', 'fitness', '筋トレ女子', 'gymgirl'],
    'workout': ['筋トレ', 'workout', 'training', 'gym', 'fitness', '筋トレ女子', 'gymgirl'],
    'pullups': ['懸垂', 'pullups', 'backworkout', 'calisthenics', 'tonedbody'],
    'posing': ['ポージング', 'posing', 'bodybuilding', 'physique', 'musclebeauty', '筋肉美'],
    'flex': ['flex', 'muscle', 'bodybuilding', 'musclebeauty', '筋肉美'],
    'muscle': ['筋肉', 'muscle', 'muscular', 'fitness', '筋肉美', 'musclebeauty'],
    'bicep': ['上腕二頭筋', 'biceps', 'arms', 'muscle', 'armday'],
    'abs': ['腹筋', 'abs', 'sixpack', 'core', 'tonedbody', 'fitchick'],
    'leg': ['脚トレ', 'legs', 'quads', 'legday', 'thickfit'],
    'back': ['背中', 'back', 'lats', 'backday', 'tonedbody'],
    'squat': ['スクワット', 'squat', 'legs', 'legday', 'thickfit'],
    'deadlift': ['デッドリフト', 'deadlift', 'powerlifting', 'strongwomen'],
    'bench': ['ベンチプレス', 'benchpress', 'chest', 'fitchick'],
    'competition': ['大会', 'competition', 'bodybuilding', 'contest', 'physique'],
    'tan': ['褐色美女', 'tanned', 'tanbody', '褐色'],
    'thick': ['むちむち', 'thickfit', 'curvy'],
}

# InstagramはFacebookよりハッシュタグ多めが効果的（最大30個・実運用は15-20個）
BASE_TAGS = [
    'musclegirl', 'muscularwoman', 'femalemuscle', 'strongwomen',
    'fbb', 'fitnessmotivation', 'gymgirl', 'thickfit',
    'musclebeauty', 'tonedbody', 'fitchick', 'girlswithmuscle',
    'fitnessgirl', 'workoutmotivation', 'gains', 'shredded',
    '筋肉女子', '筋トレ女子', 'マッスル女子', '筋肉美', '筋トレ好きと繋がりたい',
]

# キャプションテンプレート（Instagram向け・健全フィットネス路線。露骨表現なし）
CAPTION_TEMPLATES = [
    "筋肉は裏切らない💪\n毎日の積み重ねが、この身体を作る。\n\n{hashtags}",
    "圧倒的フィジーク。\nトレーニングの成果がここに🔥\n\n{hashtags}",
    "She didn't come to play.\n本気で鍛えた身体は美しい。\n\n{hashtags}",
    "鍛え抜かれた美。\nEarned, not given.💪\n\n{hashtags}",
    "Strong is the new beautiful.\n筋肉美の極み。\n\n{hashtags}",
    "Iron therapy🏋️\n筋トレは最高の自己投資。\n\n{hashtags}",
    "魅せる筋肉。\nBuilt to impress🔥\n\n{hashtags}",
    "この仕上がり、見て✨\nPeak form.\n\n{hashtags}",
    "Stronger every day.\n日々進化し続ける身体💪\n\n{hashtags}",
    "美は努力の結晶。\nNo shortcuts.\n\n{hashtags}",
]

# CTA（Instagramはキャプション内リンク非クリックのため「プロフのリンク」誘導が定石）
CTA_LINES = [
    "🔗 フル作品はプロフィールのリンクから",
    "🔗 More on the link in bio → MuscleLove",
    "🔗 Full gallery → link in bio",
]


def _load_pool():
    """content_poolからsafe_fitnessインサイトをロード。失敗時は{}（ハードコードで動く）。"""
    try:
        from pool_loader import as_insights
        return as_insights("safe_fitness", platform="instagram")
    except Exception as e:
        print(f"pool_loader unavailable (using hardcoded): {e}")
        return {}


# ===== Google Drive =====

def list_gdrive_media(folder_id):
    """Google Driveフォルダからgdownで画像/動画をダウンロード（GOOGLE_API_KEY不使用: 憲法第4条）"""
    import gdown
    dl_dir = "media"
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"Downloading from Google Drive: {url}")
    try:
        gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
    except Exception as e:
        print(f"Download error: {e}")
        return []

    media = []
    for root, dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                media.append({
                    "name": fname,
                    "local_path": os.path.join(root, fname),
                    "kind": "video" if ext in VIDEO_EXTENSIONS else "image",
                })
    return media


# ===== タグ・キャプション生成 =====

def generate_tags(name, pool=None):
    """ファイル名からハッシュタグを生成（Instagramは15-20個が最適）"""
    pool = pool or {}
    base = pool.get("recommended_tags", BASE_TAGS)
    tags = list(base)
    name_lower = name.lower().replace('-', ' ').replace('_', ' ')
    matched = set()
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in name_lower:
            for t in keyword_tags:
                if t not in matched:
                    tags.append(t)
                    matched.add(t)
    seen = set()
    unique = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return unique[:20]


def build_caption(name, tags, pool=None):
    """投稿キャプションを生成（pool優先、ハードコードフォールバック）。NG時はNoneを返す。"""
    pool = pool or {}
    hashtags = ' '.join([f'#{t}' for t in tags])

    templates = pool.get("recommended_templates", CAPTION_TEMPLATES)
    template = random.choice(templates)
    try:
        caption = template.format(hashtags=hashtags, patreon=PATREON_LINK)
    except KeyError:
        caption = template.format(hashtags=hashtags)

    # CTA（プロフィールリンク誘導）: pool由来優先
    ctas = pool.get("recommended_ctas", []) or CTA_LINES
    caption += "\n\n" + random.choice(ctas)

    # NGワード二重ガード（pool由来NG + ハードコードNG）: 固有名詞流出を機械的に阻止
    ng_words = list(pool.get("avoid_tags", [])) + NG_WORDS
    for ng in set(ng_words):
        if ng and ng in caption:
            print(f"NG word detected: {ng}")
            return None
    return caption


def is_nsfw(name):
    """ファイル名から露骨NSFWの可能性を判定（Instagram安全側に倒す）"""
    name_lower = name.lower()
    return any(kw in name_lower for kw in NSFW_KEYWORDS)


def ensure_jpeg(path):
    """Instagramの画像投稿はJPEGのみ受理。png/webp等はJPEGへ変換したパスを返す。
    Pillow未導入や変換失敗時は元パスをそのまま返す（best-effort）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        return path
    try:
        from PIL import Image
        out = os.path.splitext(path)[0] + "_ig.jpg"
        with Image.open(path) as im:
            if im.mode in ('RGBA', 'P', 'LA'):
                im = im.convert('RGB')
            im.save(out, "JPEG", quality=90)
        print(f"Converted to JPEG: {os.path.basename(out)}")
        return out
    except Exception as e:
        print(f"JPEG convert skipped ({e}); using original")
        return path


# ===== Instagram Graph API（コンテナ → publish） =====

def create_media_container(public_url, caption, kind):
    """公開URLからメディアコンテナを生成し creation_id を返す"""
    url = f"{GRAPH_API_BASE}/{IG_USER_ID}/media"
    params = {"caption": caption, "access_token": IG_ACCESS_TOKEN}
    if kind == "video":
        params["media_type"] = "REELS"
        params["video_url"] = public_url
    else:
        params["image_url"] = public_url
    print(f"Creating IG media container ({kind})...")
    resp = requests.post(url, params=params, timeout=120)
    resp.raise_for_status()
    creation_id = resp.json().get("id")
    if not creation_id:
        raise RuntimeError(f"No creation_id returned: {resp.text}")
    return creation_id


def wait_container_ready(creation_id, timeout=300, interval=10):
    """コンテナの処理完了(FINISHED)を待つ。動画で必要。画像も安全のため軽く確認。"""
    url = f"{GRAPH_API_BASE}/{creation_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(url, params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN}, timeout=60)
        resp.raise_for_status()
        status = resp.json().get("status_code")
        print(f"  container status: {status}")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            return False
        time.sleep(interval)
    print("  container processing timed out")
    return False


def publish_media(creation_id):
    """コンテナをpublishして投稿を確定。media_idを返す。"""
    url = f"{GRAPH_API_BASE}/{IG_USER_ID}/media_publish"
    params = {"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}
    print("Publishing to Instagram...")
    resp = requests.post(url, params=params, timeout=120)
    resp.raise_for_status()
    media_id = resp.json().get("id")
    print(f"Published! media_id={media_id}")
    return media_id


def verify_auth():
    """トークン/アカウントの疎通確認。ユーザー名を表示。"""
    url = f"{GRAPH_API_BASE}/{IG_USER_ID}"
    resp = requests.get(url, params={"fields": "username", "access_token": IG_ACCESS_TOKEN}, timeout=60)
    resp.raise_for_status()
    username = resp.json().get("username", "?")
    print(f"Auth OK: @{username}")
    return username


# ===== アップロードログ管理（冪等: 同じ素材を二度出さない） =====

def load_uploaded_log():
    if os.path.exists(UPLOADED_LOG):
        try:
            with open(UPLOADED_LOG, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_uploaded_log(log):
    with open(UPLOADED_LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


# ===== LINE通知 =====

def notify_line(message):
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        return
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={"to": user_id, "messages": [{"type": "text", "text": message}]},
            timeout=30,
        )
    except Exception:
        pass


# ===== メイン =====

def _print_missing_secrets(missing):
    print("=" * 60)
    print("ERROR: 必須シークレットが未設定です")
    print("=" * 60)
    print()
    print("リポジトリ → Settings → Secrets and variables → Actions → New repository secret")
    print()
    desc = {
        "IG_USER_ID": "InstagramビジネスアカウントID（数値）。README参照で取得",
        "IG_ACCESS_TOKEN": "連携Facebookページの長期アクセストークン",
        "GDRIVE_FOLDER_ID_INSTAGRAM": "安全画像を入れたGoogle DriveフォルダID（URL末尾）",
    }
    for s in missing:
        print(f"  [ ] {s}: {desc.get(s, '')}")
    print()
    print("セットアップ手順は README.md を参照してください")


def main():
    missing = []
    if not IG_USER_ID:
        missing.append("IG_USER_ID")
    if not IG_ACCESS_TOKEN:
        missing.append("IG_ACCESS_TOKEN")
    if not GDRIVE_FOLDER_ID:
        missing.append("GDRIVE_FOLDER_ID_INSTAGRAM")
    if missing:
        _print_missing_secrets(missing)
        return 1

    now = datetime.now(JST)
    print("Instagram Auto Uploader")
    print(f"IG User ID: {IG_USER_ID[:6]}...")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M JST')}\n")

    # 認証確認（トークン切れをここで早期検知）
    try:
        verify_auth()
    except requests.exceptions.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        print(f"Auth error: {e}\n{body}")
        notify_line(f"[Instagram] 認証失敗（トークン切れの可能性）\n{now.strftime('%Y-%m-%d %H:%M JST')}")
        return 1

    # Google Driveから素材取得
    media = list_gdrive_media(GDRIVE_FOLDER_ID)
    if not media:
        print("No media found!")
        return 0

    # 未投稿のみ・露骨NSFWは除外（Instagram安全側）
    uploaded = load_uploaded_log()
    available = [m for m in media if m["name"] not in uploaded and not is_nsfw(m["name"])]
    if not available:
        print("All media already uploaded (or filtered)!")
        return 0
    print(f"Available: {len(available)} / Total: {len(media)}")

    item = random.choice(available)
    print(f"Selected: {item['name']} ({item['kind']})")

    # キャプション生成（pool自動最適化 → フォールバック）
    pool = _load_pool()
    if pool:
        print(f"Pool loaded: {pool.get('updated_at_jst', '?')}")
    tags = generate_tags(item["name"], pool)
    caption = build_caption(item["name"], tags, pool)
    if caption is None:
        print("Caption contains NG words, skipping!")
        return 1
    print(f"Tags: {', '.join(tags)}")
    print(f"Caption:\n{caption}\n")

    # 画像はJPEGへ正規化（Instagramの画像投稿はJPEGのみ受理）
    local_path = item["local_path"]
    if item["kind"] == "image":
        local_path = ensure_jpeg(local_path)

    # ローカルファイル → 公開URL化（Instagram必須ステップ）
    try:
        from media_host import upload_to_public_url
        public_url = upload_to_public_url(local_path)
    except Exception as e:
        print(f"Public URL host failed: {e}")
        notify_line(f"[Instagram] 公開URL化に失敗\n{item['name']}\n{now.strftime('%Y-%m-%d %H:%M JST')}")
        return 1

    # コンテナ生成 → 処理待ち → publish
    try:
        creation_id = create_media_container(public_url, caption, item["kind"])
        # 動画は処理完了を必ず待つ。画像は基本即時だが軽く確認（失敗しても公開は試みる）
        if item["kind"] == "video":
            if not wait_container_ready(creation_id, timeout=300):
                print("Video container not ready, aborting publish")
                notify_line(f"[Instagram] 動画処理失敗\n{item['name']}\n{now.strftime('%Y-%m-%d %H:%M JST')}")
                return 1
        else:
            wait_container_ready(creation_id, timeout=60, interval=5)

        media_id = publish_media(creation_id)
        if not media_id:
            print("Publish failed!")
            notify_line(f"[Instagram] publish失敗\n{now.strftime('%Y-%m-%d %H:%M JST')}")
            return 1

        # 成功 → 冪等ログ更新
        uploaded.append(item["name"])
        save_uploaded_log(uploaded)
        remaining = len(available) - 1
        print(f"\nSuccess! Remaining: {remaining}")
        notify_line(
            f"[Instagram] 投稿成功\n素材: {item['name']}\n残り: {remaining}\n"
            f"{now.strftime('%Y-%m-%d %H:%M JST')}"
        )
        return 0

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        if e.response is not None:
            print(f"Status: {e.response.status_code}\nResponse: {e.response.text}")
        notify_line(f"[Instagram] HTTPエラー: {e}\n{now.strftime('%Y-%m-%d %H:%M JST')}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        notify_line(f"[Instagram] エラー: {e}\n{now.strftime('%Y-%m-%d %H:%M JST')}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
