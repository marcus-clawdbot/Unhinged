"""src.runner.auto_swiper

End-to-end auto swiper runner:
- capture UI XML + screenshot
- robust age extraction (AgeExtractor)
- optional fast age reject
- run complex DSPy analysis (existing analyze_profile)
- run lightweight Gemini vision rating step (optional; uses GEMINI_API_KEY)
- apply DecisionEngine
- execute like/skip via ADB helpers
- send WhatsApp notification via OpenClaw (optional)

Safety:
- dry-run prevents swipes and messages
- rate limiting enforced

Usage:
  python -m src.runner.auto_swiper --config src/config/preferences.yaml --dry-run --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

import requests
import base64

# Silence noisy Langfuse "client disabled" logs when keys are not configured.
if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
    for _name in ("langfuse", "langfuse.client", "langfuse.decorators"):
        logging.getLogger(_name).setLevel(logging.ERROR)

from src.utils import adb_helpers as adb
from src.extractors.age_extractor import AgeExtractor
from src.decision.engine import DecisionEngine, Preferences
from src.notifications.whatsapp import WhatsAppNotifier
from src.mobile_api.api import HingeAPI, SubjectPair
from src.algo.feature_extract import analyze_profile


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sleep_rate_limited(last_request_ts: float, rpm: int) -> float:
    if rpm <= 0:
        return last_request_ts
    min_delay = 60.0 / float(rpm)
    elapsed = time.time() - last_request_ts
    if elapsed < min_delay:
        time.sleep(min_delay - elapsed)
    return time.time()


def _detect_trans_woman_from_xml(xml: str) -> bool:
    """Deterministic detection based on UI XML text/content-desc.

    Hinge often shows "Trans woman" as a detail chip near age. This is more reliable than vision.
    """
    if not xml:
        return False
    s = xml.lower()
    keywords = [
        "trans woman",
        "transwoman",
        "transgender",
        "mtf",
        "m2f",
        "male-to-female",
    ]
    return any(k in s for k in keywords)


def _gemini_rate_profile(screenshot_path: str) -> Optional[dict]:
    """Lightweight vision call to get rating/body type/ethnicity.

    Returns dict with keys expected by DecisionEngine:
    - is_profile, name, rating, slim_athletic, ethnicity, ethnicity_ok, vibe, red_flags, reason

    Requires env var GEMINI_API_KEY. If not set, returns None.
    """

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    with open(screenshot_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = """Analyze this dating app profile screenshot. Return ONLY valid JSON (no markdown):
{
  \"is_profile\": true/false,
  \"name\": \"name or null if not visible\",
  \"is_trans_woman\": true/false,
  \"slim_athletic\": true/false,
  \"ethnicity\": \"description\",
  \"ethnicity_ok\": true/false,
  \"rating\": 1-10,
  \"vibe\": \"positive/neutral/negative\",
  \"red_flags\": [\"list of any red flags\"],
  \"reason\": \"brief reason\"
}

If NOT a dating profile (home screen, other app, etc), set is_profile=false.

Mark \"is_trans_woman\" true if the profile indicates transgender / trans woman / MtF (including explicit text cues)."""
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500},
    }

    resp = requests.post(
        f"{url}?key={api_key}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code == 429:
        return None
    if resp.status_code != 200:
        return None

    result = resp.json()
    text = (
        result.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )

    # strip code fences if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except Exception:
        return None


def _capture_profile_photos(
    api: HingeAPI,
    max_scrolls: int = 4,
    out_dir: str = "photo_dump",
    max_photos: int = 6,
    max_seconds: float = 20.0,
) -> list[str]:
    """Iterate subjects + scroll; capture cropped photos.

    Hard-stops to avoid getting stuck at the bottom of a long profile.
    """

    import glob

    os.makedirs(out_dir, exist_ok=True)

    # clear previous
    for f in glob.glob(os.path.join(out_dir, "photo_*.png")):
        try:
            os.remove(f)
        except Exception:
            pass

    start = time.time()
    processed_bounds = set()
    no_new_streak = 0

    def is_valid(bounds) -> bool:
        if not bounds:
            return False
        x1, y1, x2, y2 = bounds
        w = x2 - x1
        h = y2 - y1
        return h > 0 and (w / h) <= 1.5

    def current_photos() -> list[str]:
        return sorted([p for p in glob.glob(os.path.join(out_dir, "photo_*.png"))])

    def process_subjects() -> int:
        new_count = 0
        subjects = api.get_all_subjects()
        for subject_str, content, bounds in subjects:
            if ("[Image]" in subject_str) or ("photo" in subject_str.lower()):
                if bounds and bounds not in processed_bounds and is_valid(bounds):
                    processed_bounds.add(bounds)
                    api.capture_subject_photo(SubjectPair(subject_str, content, None, bounds), out_dir)
                    new_count += 1
                    if len(processed_bounds) >= max_photos:
                        break
        return new_count

    new0 = process_subjects()
    photos = current_photos()
    print(f"[CAPTURE] start photos={len(photos)} new={new0}")

    for i in range(max_scrolls):
        if time.time() - start > max_seconds:
            print(f"[CAPTURE] timeout after {max_seconds}s")
            break
        if len(photos) >= max_photos:
            print(f"[CAPTURE] reached max_photos={max_photos}")
            break

        adb.swipe(540, 1800, 540, 600, 500)
        time.sleep(0.9)

        # refresh api
        dump_path = adb.get_ui_dump(0)
        api.xml_path = dump_path
        api._update_profile_info()
        api.subject_pairs = api._parse_subjects_and_hearts()

        new_n = process_subjects()
        photos = current_photos()
        print(f"[CAPTURE] scroll={i+1}/{max_scrolls} photos={len(photos)} new={new_n}")

        if new_n == 0:
            no_new_streak += 1
        else:
            no_new_streak = 0

        if no_new_streak >= 2:
            print("[CAPTURE] no new photos for 2 scrolls; stopping")
            break

    return photos


async def _run_one_iteration(
    prefs: Preferences,
    engine: DecisionEngine,
    notifier: WhatsAppNotifier,
    age_extractor: AgeExtractor,
    dry_run: bool,
    aggressive: bool,
    last_gemini_ts: float,
) -> tuple[bool, float]:
    """Returns (did_process_profile, last_gemini_ts)."""

    xml = adb.get_ui_xml()
    if not xml:
        print("[WARN] No UI XML; skipping iteration")
        return False, last_gemini_ts

    # Always reset to top of profile before doing anything else.
    adb.scroll_to_top()
    xml = adb.get_ui_xml() or xml

    # close blockers
    if adb.is_like_modal_open(xml):
        adb.close_modal_if_open(xml)
        xml = adb.get_ui_xml() or xml
    if adb.is_safety_center_popup(xml):
        adb.close_safety_center_if_open(xml)
        xml = adb.get_ui_xml() or xml

    if not adb.is_hinge_profile(xml):
        adb.open_hinge()
        time.sleep(2)
        xml = adb.get_ui_xml() or ""
        if not adb.is_hinge_profile(xml):
            return False, last_gemini_ts

    screenshot_path = adb.capture_screenshot_fast()

    # PRIME the profile: Hinge often lazy-loads age/details only after a small scroll.
    # We scroll first, then re-dump UI and re-screenshot, then attempt age extraction.
    try:
        adb.prime_profile_details()
        time.sleep(0.5)
    except Exception:
        pass

    xml2 = adb.get_ui_xml() or xml
    screenshot_path2 = adb.capture_screenshot_fast() or screenshot_path

    # Deterministic trans-woman detection from UI XML (more reliable than vision).
    xml_is_trans = _detect_trans_woman_from_xml(xml2)

    age = None
    # Age extractor should be best-effort; don't let OCR issues spam/kill loop.
    if screenshot_path2:
        try:
            age = age_extractor.extract(xml2, screenshot_path2)
        except Exception as e:
            print(f"[WARN] age_extractor failed: {e}")
            age = None

    # Fallback: try the original artifacts too
    if age is None and screenshot_path:
        try:
            age = age_extractor.extract(xml, screenshot_path)
        except Exception:
            age = None

    # fast age filter
    if age is not None and not engine.quick_age_filter(age):
        if not dry_run:
            adb.execute_skip(xml)
        return True, last_gemini_ts

    # expensive analysis
    dump_path = adb.get_ui_dump(0)
    api = HingeAPI(dump_path)
    profile_info = api.get_profile_info()

    # Fallback: if AgeExtractor couldn't find age but HingeAPI did, use it.
    if age is None:
        api_age = getattr(profile_info, "age", None)
        if api_age is not None:
            age = api_age

    photo_paths = _capture_profile_photos(api, max_photos=prefs.capture_max_photos)
    if not photo_paths:
        print("[WARN] No photo crops captured; cannot analyze. Skipping.")
        if not dry_run:
            adb.execute_skip(xml)
        return True, last_gemini_ts

    # analyze_profile can be slow / can fail if LLM not configured.
    try:
        profile = await analyze_profile(profile_images=photo_paths, profile_info=profile_info)
    except Exception as e:
        print(f"[WARN] analyze_profile failed (continuing with defaults): {e}")
        profile = None

    # build ai_result for DecisionEngine using gemini rating step (optional)
    ai_result = {
        "is_profile": True,
        "name": (getattr(profile, "name", None) if profile else None) or (profile_info.name or None),
        "age": age,
        "is_trans_woman": bool(xml_is_trans),
        "reason": "DSPy profile analyzed" if profile else "DSPy analysis unavailable; using defaults",
        "red_flags": [],
        # defaults; may be overwritten
        "rating": None,
        "slim_athletic": True,
        "ethnicity_ok": True,
        "ethnicity": None,
    }

    if screenshot_path and os.environ.get("GEMINI_API_KEY"):
        last_gemini_ts = _sleep_rate_limited(last_gemini_ts, prefs.max_requests_per_minute)
        gem = _gemini_rate_profile(screenshot_path)
        if gem:
            ai_result.update(gem)
            # Never let vision override the deterministic XML detection.
            if xml_is_trans:
                ai_result["is_trans_woman"] = True

    decision = engine.decide(age=age, ai_result=ai_result, override_like=aggressive)

    # Always print a concise decision line (especially important for dry-run).
    # Example: [DRY] age=34 rating=7 slim=True eth_ok=True => LIKE (reason...)
    rating = ai_result.get("rating")
    slim = ai_result.get("slim_athletic")
    eth_ok = ai_result.get("ethnicity_ok")
    name = ai_result.get("name")
    prefix = "[DRY]" if dry_run else "[LIVE]"
    print(
        f"{prefix} name={name!r} age={age} rating={rating} slim_athletic={slim} ethnicity_ok={eth_ok} => {decision.action} :: {decision.reason}"
    )

    if dry_run:
        return True, last_gemini_ts

    if decision.action == "LIKE":
        ok, _ = adb.execute_like(xml)
        if ok and prefs.notify_on_like and prefs.whatsapp_group_jid:
            notifier.send_like_notification(
                name=decision.name,
                rating=decision.rating,
                reason=decision.reason,
                age=decision.age,
                screenshot_path=screenshot_path if prefs.send_screenshot_with_like else None,
            )
    elif decision.action in ("PASS", "SKIP"):
        adb.execute_skip(xml)
        if prefs.notify_on_pass and prefs.whatsapp_group_jid:
            notifier.send_pass_notification(name=decision.name, reason=decision.reason, age=decision.age)

    return True, last_gemini_ts


def main():
    parser = argparse.ArgumentParser(description="Unhinged auto swiper")
    parser.add_argument("--config", default="src/config/preferences.yaml", help="Path to preferences YAML")
    parser.add_argument("--dry-run", action="store_true", help="Do not execute swipes or send WhatsApp")
    parser.add_argument("--limit", type=int, default=None, help="Max profiles to process")
    parser.add_argument("--hours", type=float, default=None, help="Run for N hours")
    parser.add_argument("--aggressive", action="store_true", help="Force LIKE regardless of filters")
    args = parser.parse_args()

    prefs = Preferences.from_yaml(args.config)
    engine = DecisionEngine(preferences=prefs)
    age_extractor = AgeExtractor()

    notifier = WhatsAppNotifier(
        group_jid=prefs.whatsapp_group_jid or "",
        enabled=(not args.dry_run) and bool(prefs.whatsapp_group_jid) and (prefs.notify_on_like or prefs.notify_on_pass),
    )

    start = _now_utc()
    end = None
    if args.hours is not None:
        end = start + timedelta(hours=float(args.hours))

    processed = 0
    last_gemini_ts = 0.0

    try:
        while True:
            if args.limit is not None and processed >= args.limit:
                break
            if end is not None and _now_utc() >= end:
                break

            did, last_gemini_ts = asyncio.run(
                _run_one_iteration(
                    prefs=prefs,
                    engine=engine,
                    notifier=notifier,
                    age_extractor=age_extractor,
                    dry_run=bool(args.dry_run),
                    aggressive=bool(args.aggressive),
                    last_gemini_ts=last_gemini_ts,
                )
            )
            if did:
                processed += 1
            time.sleep(1)

    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
