import os
from types import SimpleNamespace

import pytest

import src.runner.auto_swiper as auto_swiper


@pytest.mark.asyncio
async def test_fast_mode_fail_closed_missing_key(monkeypatch):
    """If GEMINI_API_KEY missing in fast mode => should PASS/SKIP (no like).

    We model this by asserting adb.execute_skip is called and adb.execute_like is not.
    """

    # env
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    # adb stubs
    monkeypatch.setattr(auto_swiper.adb, "get_ui_xml", lambda: "<xml>hinge profile</xml>")
    monkeypatch.setattr(auto_swiper.adb, "scroll_to_top", lambda: None)
    monkeypatch.setattr(auto_swiper.adb, "is_like_modal_open", lambda xml: False)
    monkeypatch.setattr(auto_swiper.adb, "close_modal_if_open", lambda xml: None)
    monkeypatch.setattr(auto_swiper.adb, "is_safety_center_popup", lambda xml: False)
    monkeypatch.setattr(auto_swiper.adb, "close_safety_center_if_open", lambda xml: None)
    monkeypatch.setattr(auto_swiper.adb, "is_hinge_profile", lambda xml: True)
    monkeypatch.setattr(auto_swiper.adb, "open_hinge", lambda: None)
    monkeypatch.setattr(auto_swiper.adb, "capture_screenshot_fast", lambda: "/tmp/shot.png")
    monkeypatch.setattr(auto_swiper.adb, "prime_profile_details", lambda: None)
    monkeypatch.setattr(auto_swiper.adb, "get_ui_dump", lambda _: "/tmp/dump.xml")

    did_like = {"v": False}
    did_skip = {"v": False}

    def _like(xml):
        did_like["v"] = True
        return True, ""

    def _skip(xml):
        did_skip["v"] = True
        return True

    monkeypatch.setattr(auto_swiper.adb, "execute_like", _like)
    monkeypatch.setattr(auto_swiper.adb, "execute_skip", _skip)

    # other deps
    class DummyEngine:
        def quick_age_filter(self, age):
            return True

        def decide(self, age, ai_result, override_like=False):
            return SimpleNamespace(
                action="LIKE",
                reason="",
                name=ai_result.get("name"),
                rating=ai_result.get("rating"),
                age=age,
            )

    class DummyPrefs:
        max_requests_per_minute = 60
        capture_max_photos = 3
        notify_on_like = False
        notify_on_pass = False
        whatsapp_group_jid = ""
        send_screenshot_with_like = False

    class DummyNotifier:
        def send_like_notification(self, **kwargs):
            raise AssertionError("should not notify in this test")

        def send_pass_notification(self, **kwargs):
            raise AssertionError("should not notify in this test")

    class DummyAgeExtractor:
        def extract(self, xml, screenshot):
            return 30

    class DummyHingeAPI:
        def __init__(self, xml_path):
            self.xml_path = xml_path

        def get_profile_info(self):
            return SimpleNamespace(name="A", age=30)

    monkeypatch.setattr(auto_swiper, "HingeAPI", DummyHingeAPI)

    did, _ = await auto_swiper._run_one_iteration(
        prefs=DummyPrefs(),
        engine=DummyEngine(),
        notifier=DummyNotifier(),
        age_extractor=DummyAgeExtractor(),
        dry_run=False,
        aggressive=False,
        last_gemini_ts=0.0,
        fast=True,
    )

    assert did is True
    assert did_skip["v"] is True
    assert did_like["v"] is False
