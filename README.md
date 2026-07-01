# instagram-auto-uploader

Google Drive の「Instagramに出して安全な画像/動画」からランダムに1つ選び、
Instagram Graph API で **無人自動投稿** する。Tumblr/Facebook 自動投稿と同じ設計思想
（Drive→ランダム→タグ/キャプション自動生成→投稿→冪等ログ）。M国憲法「完全自動運営」準拠。

## Tumblr/Facebook版との違い（実装の肝）

Instagram Graph API は **ローカルファイルを直接アップロードできない**。
「公開URLを指定 → メディアコンテナ生成 → publish」の2段階しか無く、しかも
Google Drive の `uc?export=download` URL は Instagram 側が取得に失敗する。

そこで本ツールは以下の順で処理する:

```
Drive(gdown, キー不要) → ランダム選択 → (画像はJPEGへ正規化)
  → 匿名ホスト(litterbox 72h → catbox)で公開URL化   ← ★ここがInstagram専用の追加ステップ
  → /{ig-user-id}/media でコンテナ生成
  → (動画は status_code=FINISHED まで待機)
  → /{ig-user-id}/media_publish で公開
  → uploaded_instagram.json に記録（冪等: 同じ素材を二度出さない）
```

## ファイル構成

| ファイル | 役割 |
|---|---|
| `upload.py` | 本体（Drive→選択→公開URL→コンテナ→publish、NGワード/NSFWガード、LINE通知） |
| `media_host.py` | ローカルファイル→公開URL（litterbox/catbox・**APIキー不要**） |
| `pool_loader.py` | `dashboard/autonomy` の content_pool を読む（毎日自動最適化・全uploader共通） |
| `token_refresh.py` | 60日で切れるトークンの点検・自動延長（月次） |
| `.github/workflows/instagram-post.yml` | JST 12:00/20:00 投稿 + 月次トークン点検 + 2回リトライ |
| `requirements.txt` | requests / gdown / Pillow |

## セットアップ（初回のみ・人間の作業）

Instagramは仕様上、Tumblrのような「トークンだけ」では投稿できず、下記の一度きりの準備が要る。

### 1. アカウント準備
1. 投稿先Instagramを **ビジネス or クリエイター** アカウントにする（アプリ内で切替可）
2. そのInstagramを **Facebookページ** に連携する（Instagram設定→ページとリンク）

### 2. アクセストークンとIDを取得
1. https://developers.facebook.com/apps/ でアプリ作成（用途: その他 / ビジネス）
2. 「Instagram Graph API」製品を追加
3. https://developers.facebook.com/tools/explorer/ (Graph API Explorer) で自分のアプリを選び、
   権限 `instagram_basic` `instagram_content_publish` `pages_show_list` `pages_read_engagement`
   を付けてユーザートークンを発行
4. `GET /me/accounts` → 対象ページの `access_token`（＝ページトークン）を控える
5. `GET /{page-id}?fields=instagram_business_account` → 返る `id` が **IG_USER_ID**
6. ページトークンは短命。デバッグツールで長期トークン（60日）へ交換して **IG_ACCESS_TOKEN** とする
   （`token_refresh.py` が以後の延長を担当）

> 詳細な公式手順: https://developers.facebook.com/docs/instagram-api/getting-started

### 3. GitHub Secrets を設定
リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Secret | 必須 | 内容 |
|---|---|---|
| `IG_USER_ID` | ✅ | Instagramビジネスアカウントの数値ID（手順2-5） |
| `IG_ACCESS_TOKEN` | ✅ | ページ長期アクセストークン（手順2-6） |
| `GDRIVE_FOLDER_ID_INSTAGRAM` | ✅ | **安全画像だけ**を入れたDriveフォルダID（URL末尾） |
| `FB_APP_ID` / `FB_APP_SECRET` | 推奨 | トークン自動延長に使用（未設定なら手動更新） |
| `LINE_CHANNEL_TOKEN` / `LINE_USER_ID` | 任意 | 成否・トークン失効をLINE通知 |

> **GOOGLE_API_KEY は不要**（gdownでキーレス取得。憲法第4条）。旧実装の依存は撤去済み。

### 4. 動かす
`Actions → Instagram Auto Post → Run workflow` で手動実行してテスト。以後はcronで無人投稿。

## コンテンツ安全設計（重要）

Instagramは**ヌード/性的表現でBAN**になる。本ブランドはアダルト路線だが、Instagramには
gif_factory の「**非エロ＝主要SNSに出して安全**」レーンの素材だけを出す想定:

- `GDRIVE_FOLDER_ID_INSTAGRAM` には **安全画像専用フォルダ** を指定する（アダルトと分離）
- ファイル名に露骨語（nude/erotic/エロ/裸 等）を含む素材は自動スキップ
- キャプションは健全フィットネス路線（Tumblrの露骨ペルソナは使わない）
- content_pool の `safe_fitness` レーンで毎日自動最適化

## 制限・注意

- Content Publishing は **24時間で50投稿まで**（本ツールは1日2回なので余裕）
- 画像は **JPEGのみ受理**（png/webpは自動でJPEG変換して投稿）
- 動画は **REELS** として投稿（mp4/mov、処理完了を待ってからpublish）。GIFは不可
- トークンは60日で失効 → 月次 `token_refresh.py` が延長（`FB_APP_ID/SECRET`設定時）。
  更新トークンのSecret反映は手動（LINEで通知）。
- Instagramキャプション内のリンクは非クリック → CTAは「プロフィールのリンク」誘導
