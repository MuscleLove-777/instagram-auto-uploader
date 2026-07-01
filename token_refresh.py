# -*- coding: utf-8 -*-
"""
Instagram アクセストークン自動更新スクリプト

Instagram Graph APIのアクセストークンは60日で期限切れになる。
このスクリプトを定期実行（月1回など）してトークンを延長する。

使い方:
  python token_refresh.py
"""
import os
import sys
import requests
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
GRAPH_API_VERSION = "v21.0"


def refresh_token(current_token):
    """長期トークンを更新（60日延長）"""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": os.environ.get("FB_APP_ID", ""),
        "client_secret": os.environ.get("FB_APP_SECRET", ""),
        "fb_exchange_token": current_token,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    new_token = data["access_token"]
    expires_in = data.get("expires_in", 0)
    expires_days = expires_in // 86400
    print(f"Token refreshed! Expires in {expires_days} days")
    return new_token


def check_token_info(token):
    """トークンの有効性と期限を確認"""
    url = f"https://graph.facebook.com/debug_token"
    params = {
        "input_token": token,
        "access_token": token,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get("data", {})

    is_valid = data.get("is_valid", False)
    expires_at = data.get("expires_at", 0)

    if expires_at > 0:
        expire_date = datetime.fromtimestamp(expires_at, tz=JST)
        days_left = (expire_date - datetime.now(JST)).days
        print(f"Token valid: {is_valid}")
        print(f"Expires: {expire_date.strftime('%Y-%m-%d %H:%M JST')}")
        print(f"Days left: {days_left}")
        return days_left
    else:
        print(f"Token valid: {is_valid}")
        print("No expiration (never expires)")
        return 999


def notify_line(message):
    """LINE通知（無人運用でトークン更新の要否を人へ知らせる生命線）"""
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


def main():
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token:
        print("Error: IG_ACCESS_TOKEN not set")
        return 1

    print(f"Checking token... ({datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')})")
    days_left = check_token_info(token)

    if days_left < 14:
        print("\nToken expiring soon! Refreshing...")
        app_id = os.environ.get("FB_APP_ID", "")
        app_secret = os.environ.get("FB_APP_SECRET", "")
        if not app_id or not app_secret:
            print("Error: FB_APP_ID and FB_APP_SECRET required for refresh")
            print("Set these in GitHub Secrets or environment variables")
            notify_line(
                "[Instagram] トークンが14日以内に失効しますが自動延長できません。\n"
                "FB_APP_ID / FB_APP_SECRET を設定するか、手動でトークンを更新してください。"
            )
            return 1
        new_token = refresh_token(token)
        print(f"\nNew token (first 20 chars): {new_token[:20]}...")
        print("Update IG_ACCESS_TOKEN in GitHub Secrets with this new token!")
        notify_line(
            "[Instagram] 新しいアクセストークンを発行しました。\n"
            "GitHub Secrets の IG_ACCESS_TOKEN を更新してください（先頭: "
            f"{new_token[:12]}...）"
        )
        return 0
    else:
        print(f"\nToken OK. No refresh needed. ({days_left} days left)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
