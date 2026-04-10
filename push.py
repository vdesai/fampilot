"""
FamPilot — Web Push notifications module.

Sends notifications to family members via Web Push API (VAPID).
"""

import os
import json
import logging
from typing import Optional

import db

logger = logging.getLogger(__name__)

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:support@fampilot.app")


def is_configured() -> bool:
    return bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)


def _send_one(subscription_info: dict, payload: dict) -> bool:
    """Send a single push notification. Returns True on success."""
    if not is_configured():
        return False
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed")
        return False

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return True
    except WebPushException as e:
        # 404 or 410 = subscription is dead, delete it
        if e.response is not None and e.response.status_code in (404, 410):
            db.delete_push_subscription(subscription_info["endpoint"])
            logger.info(f"Deleted dead subscription: {subscription_info['endpoint'][:50]}")
        else:
            logger.error(f"Push failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Push error: {e}")
        return False


def send_to_member(member_id: str, title: str, body: str,
                   url: Optional[str] = None, tag: Optional[str] = None) -> int:
    """Send a push notification to all devices of a member. Returns success count."""
    subs = db.get_push_subscriptions_for_member(member_id)
    return _fan_out(subs, title, body, url, tag)


def send_to_family(family_id: str, title: str, body: str,
                   url: Optional[str] = None, tag: Optional[str] = None,
                   exclude_member_id: Optional[str] = None) -> int:
    """Send a push notification to all members of a family. Returns success count."""
    subs = db.get_push_subscriptions_for_family(family_id)
    if exclude_member_id:
        subs = [s for s in subs if s["member_id"] != exclude_member_id]
    return _fan_out(subs, title, body, url, tag)


def _fan_out(subs: list, title: str, body: str,
             url: Optional[str], tag: Optional[str]) -> int:
    payload = {"title": title, "body": body}
    if url:
        payload["url"] = url
    if tag:
        payload["tag"] = tag

    success = 0
    for sub in subs:
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {
                "p256dh": sub["p256dh"],
                "auth": sub["auth"],
            }
        }
        if _send_one(sub_info, payload):
            success += 1
            db.mark_push_success(sub["endpoint"])
    return success
