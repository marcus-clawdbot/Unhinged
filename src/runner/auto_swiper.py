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
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

import requests
import base64

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
  \"body_type_score\": 1-10,
  \"ethnicity\": \"description\",
  \"ethnicity_ok\": true/false,
  \"rating\": 1-10,
  \"vibe\": \"positive/neutral/negative\",
  \"red_flags\": [\"list of any red flags\"],
  \"reason\": \"brief reason\"
}

body_type_score: 1=very overweight, 5=average, 7=fit, 10=very athletic.

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

    img_size_kb = len(image_b64) * 3 // 4 // 1024
    print(f"[GEMINI] POST {model} | image={screenshot_path} ({img_size_kb} KB) | temp=0.1 maxTokens=500")
    print(f"[GEMINI] prompt={prompt[:120]}...")

    resp = requests.post(
        f"{url}?key={api_key}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    print(f"[GEMINI] status={resp.status_code}")

    if resp.status_code == 429:
        print("[GEMINI] rate limited (429)")
        return None
    if resp.status_code != 200:
        print(f"[GEMINI] error body={resp.text[:300]}")
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
        parsed = json.loads(text)
        print(f"[GEMINI] response={json.dumps(parsed, indent=2)}")
        return parsed
    except Exception:
        print(f"[GEMINI] JSON parse failed, raw={text[:300]}")
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
    fast: bool = False,
) -> tuple[bool, float]:
    """Returns (did_process_profile, last_gemini_ts)."""

    # 1. Initial XML dump + basic checks
    xml = adb.get_ui_xml()
    if not xml:
        print("[WARN] No UI XML; skipping iteration")
        return False, last_gemini_ts

    # Close blockers first (before any navigation)
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
            print("[WARN] Not a Hinge profile after opening; skipping")
            return False, last_gemini_ts

    # 2. Prime profile to reveal lazy-loaded detail chips (age, gender, etc.)
    adb.prime_profile_details()
    time.sleep(0.4)

    # 3. Capture the primed XML (has detail chips visible) for age extraction + profile info.
    xml_primed = adb.get_ui_xml() or xml
    xml_is_trans = _detect_trans_woman_from_xml(xml_primed)

    # 4. Age extraction from the primed XML (best-effort)
    age = None
    try:
        age = age_extractor.extract(xml_primed)
    except Exception as e:
        print(f"[WARN] age_extractor failed: {e}")

    # 5. Fast age filter — skip expensive analysis if age is out of range
    if age is not None and not engine.quick_age_filter(age):
        if not dry_run:
            adb.execute_skip(xml)
        return True, last_gemini_ts

    # 6. Save primed XML to file for HingeAPI parsing
    dump_path = adb.get_ui_dump(0)
    api = HingeAPI(dump_path)
    profile_info = api.get_profile_info()

    # 7. Scroll to top for screenshot (main photo visible for Gemini)
    adb.scroll_to_top(max_swipes=3)
    time.sleep(0.3)
    screenshot_path = adb.capture_screenshot_fast()

    # Fallback: if AgeExtractor couldn't find age but HingeAPI did, use it.
    if age is None:
        api_age = getattr(profile_info, "age", None)
        if api_age is not None:
            age = api_age

    # Apply quick age filter AGAIN after HingeAPI fallback.
    if age is not None and not engine.quick_age_filter(age):
        if not dry_run:
            adb.execute_skip(xml)
        return True, last_gemini_ts

    if fast:
        # FAST MODE: single screenshot + Gemini only; no photo capture, no DSPy.
        # Fail-closed: if GEMINI_API_KEY missing or Gemini yields no rating => PASS/SKIP.
        if not os.environ.get("GEMINI_API_KEY"):
            if not dry_run:
                adb.execute_skip(xml)
            return True, last_gemini_ts

        last_gemini_ts = _sleep_rate_limited(last_gemini_ts, prefs.max_requests_per_minute)
        gem = _gemini_rate_profile(screenshot_path)
        rating = gem.get("rating") if isinstance(gem, dict) else None
        if rating is None:
            if not dry_run:
                adb.execute_skip(xml)
            return True, last_gemini_ts

        # Ethnicity handling: if Gemini can't determine ethnicity, treat it as "unknown" and allow it.
        eth = gem.get("ethnicity") if isinstance(gem, dict) else None
        eth_ok_raw = gem.get("ethnicity_ok") if isinstance(gem, dict) else None
        if eth is None:
            eth = "unknown"
            eth_ok = True
        else:
            eth_ok = bool(eth_ok_raw) if eth_ok_raw is not None else True

        ai_result = {
            "is_profile": True,
            "name": (gem.get("name") if isinstance(gem, dict) else None) or (profile_info.name or None),
            "age": age,
            "is_trans_woman": bool(xml_is_trans),
            "rating": rating,
            "body_type_score": gem.get("body_type_score") if isinstance(gem, dict) else None,
            "ethnicity_ok": eth_ok,
            "ethnicity": eth,
            "reason": gem.get("reason") if isinstance(gem, dict) else "Gemini fast-mode rating",
            "red_flags": gem.get("red_flags") if isinstance(gem, dict) and isinstance(gem.get("red_flags"), list) else [],
        }
        # Never let vision override the deterministic XML detection.
        if xml_is_trans:
            ai_result["is_trans_woman"] = True

    else:
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
            "body_type_score": None,
            "ethnicity_ok": True,
            "ethnicity": None,
        }

        if screenshot_path and os.environ.get("GEMINI_API_KEY"):
            last_gemini_ts = _sleep_rate_limited(last_gemini_ts, prefs.max_requests_per_minute)
            gem = _gemini_rate_profile(screenshot_path)
            if gem:
                ai_result.update(gem)

                # Lenient normalization: if ethnicity is missing/null, treat as unknown+allowed.
                # This avoids false rejections when Gemini fails to classify ethnicity.
                if ai_result.get("ethnicity") is None:
                    ai_result["ethnicity"] = "unknown"
                    ai_result["ethnicity_ok"] = True

                # Never let vision override the deterministic XML detection.
                if xml_is_trans:
                    ai_result["is_trans_woman"] = True

    decision = engine.decide(age=age, ai_result=ai_result, override_like=aggressive)

    # Always print a concise decision line (especially important for dry-run).
    # Example: [DRY] age=34 rating=7 slim=True eth_ok=True => LIKE (reason...)
    rating = ai_result.get("rating")
    body_score = ai_result.get("body_type_score")
    eth_ok = ai_result.get("ethnicity_ok")
    name = ai_result.get("name")
    prefix = "[DRY]" if dry_run else "[LIVE]"
    print(
        f"{prefix} name={name!r} age={age} rating={rating} body_score={body_score} ethnicity_ok={eth_ok} => {decision.action} :: {decision.reason}"
    )

    if dry_run:
        return True, last_gemini_ts

    if decision.action == "LIKE":
        ok, _ = adb.execute_like(xml)
        print(f"[NOTIFY] LIKE executed: ok={ok} notify_on_like={prefs.notify_on_like} group_jid={prefs.whatsapp_group_jid!r} notifier_enabled={notifier.enabled}")
        if ok and prefs.notify_on_like and prefs.whatsapp_group_jid:
            result = notifier.send_like_notification(
                name=decision.name,
                rating=decision.rating,
                reason=decision.reason,
                age=decision.age,
                screenshot_path=screenshot_path if prefs.send_screenshot_with_like else None,
            )
            print(f"[NOTIFY] send_like_notification result: success={result.success} error={result.error}")
        else:
            print(f"[NOTIFY] skipped: ok={ok} notify_on_like={prefs.notify_on_like} has_jid={bool(prefs.whatsapp_group_jid)}")
    elif decision.action in ("PASS", "SKIP"):
        adb.execute_skip(xml)
        if prefs.notify_on_pass and prefs.whatsapp_group_jid:
            result = notifier.send_pass_notification(name=decision.name, reason=decision.reason, age=decision.age)
            print(f"[NOTIFY] send_pass_notification result: success={result.success} error={result.error}")

    return True, last_gemini_ts


def main():
    print("[DEBUG] main() started")
    parser = argparse.ArgumentParser(description="Unhinged auto swiper")
    parser.add_argument("--config", default="src/config/preferences.yaml", help="Path to preferences YAML")
    parser.add_argument("--dry-run", action="store_true", help="Do not execute swipes or send WhatsApp")
    parser.add_argument("--limit", type=int, default=None, help="Max profiles to process")
    parser.add_argument("--hours", type=float, default=None, help="Run for N hours")
    parser.add_argument("--aggressive", action="store_true", help="Force LIKE regardless of filters")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: single screenshot + Gemini only (skips DSPy analysis and photo capture)",
    )
    args = parser.parse_args()
    print("[DEBUG] args parsed")

    print('[DEBUG] Loading preferences...')
    prefs = Preferences.from_yaml(args.config)
    print('[DEBUG] Creating DecisionEngine...')
    engine = DecisionEngine(preferences=prefs)
    print('[DEBUG] Creating AgeExtractor...')
    age_extractor = AgeExtractor()
    notifier_enabled = (not args.dry_run) and bool(prefs.whatsapp_group_jid) and (prefs.notify_on_like or prefs.notify_on_pass)
    print(f'[DEBUG] Creating WhatsAppNotifier... enabled={notifier_enabled} dry_run={args.dry_run} jid={prefs.whatsapp_group_jid!r} notify_on_like={prefs.notify_on_like}')
    notifier = WhatsAppNotifier(
        group_jid=prefs.whatsapp_group_jid or "",
        enabled=notifier_enabled,
    )
    print('[DEBUG] Initialisation complete')

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

            print(f'[DEBUG] Starting iteration {processed}')
            did, last_gemini_ts = asyncio.run(
                _run_one_iteration(
                    prefs=prefs,
                    engine=engine,
                    notifier=notifier,
                    age_extractor=age_extractor,
                    dry_run=bool(args.dry_run),
                    aggressive=bool(args.aggressive),
                    last_gemini_ts=last_gemini_ts,
                    fast=bool(args.fast),
                )
            )
            print(f'[DEBUG] Iteration result: did={did}')
            if did:
                processed += 1
            time.sleep(1)

    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
