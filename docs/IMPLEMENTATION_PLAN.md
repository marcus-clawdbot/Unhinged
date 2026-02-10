# Implementation Plan: Auto-Decision Engine + WhatsApp Integration

**Branch:** `feature/auto-decision-whatsapp`  
**Date:** 2026-02-10  
**Status:** PLANNING (awaiting Boss review)  
**WhatsApp Group:** `120363424286297551@g.us` (Hinge Liker group)

---

## Executive Summary

Port the proven auto-liking logic from `hinge_gemini_runner.py` into the `unhinged_repo` SaaS architecture:

1. **Robust age extraction** with 4-layer cascading fallback
2. **Decision engine** with user preferences (age, ethnicity, body type, rating)
3. **WhatsApp notifications** via OpenClaw
4. **ADB action automation** (like/pass buttons)
5. **Dry-run mode** for testing

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│              AUTO SWIPER (src/runner/auto_swiper.py)     │
├──────────────────────────────────────────────────────────┤
│  1. Capture UI dump + screenshot                         │
│  2. Extract age (4-method cascade)                       │
│  3. Fast age filter → skip if out of range              │
│  4. Run DSPy photo analysis (if age passes)              │
│  5. Apply DecisionEngine preferences                     │
│  6. Execute ADB action (like/pass)                       │
│  7. Send WhatsApp notification                           │
│  8. Update metrics, loop                                 │
└──────────────────────────────────────────────────────────┘
```

---

## 1. Robust Age Extraction

### Current Weakness
- Only uses UI XML label matching (`content-desc="Age"`)
- Fails on ~30% of profiles

### Solution: 4-Method Cascade

**NEW FILE:** `src/extractors/age_extractor.py`

```python
class AgeExtractor:
    def extract(self, xml_dump: str, screenshot_path: str, ai_result: dict = None) -> int | None:
        # 1. XML label matching (existing)
        age = self._extract_from_labels(xml_dump)
        if age: return age
        
        # 2. Header positional parsing (from hinge_gemini_runner)
        age = self._extract_from_header(xml_dump)
        if age: return age
        
        # 3. OCR fallback (pytesseract)
        age = self._extract_from_ocr(screenshot_path)
        if age: return age
        
        # 4. AI vision (already in photo analysis)
        if ai_result: return ai_result.get("age")
        
        return None
```

**Key Methods:**
- `_extract_from_header()`: Port from `hinge_gemini_runner.py` lines 377-431
- `_extract_from_ocr()`: Port from lines 117-169, uses pytesseract on screen bands

---

## 2. Decision Engine

### NEW FILE: `src/config/preferences.yaml`

```yaml
# User preferences for auto-swiping
age:
  min: 18
  max: 35

rating:
  min_threshold: 6.0  # 1-10 scale

body_type:
  require_slim_athletic: true

ethnicity:
  allowed:
    - caucasian
  # Set to null to disable ethnicity filter

notifications:
  whatsapp_group_jid: "120363424286297551@g.us"
  notify_on_like: true
  notify_on_pass: false  # Don't spam group with passes
  send_screenshot_with_like: true

rate_limits:
  max_requests_per_minute: 15  # Gemini free tier
  max_requests_per_day: 1500
```

### NEW FILE: `src/decision/engine.py`

```python
@dataclass
class Decision:
    action: str  # "LIKE", "PASS", "SKIP"
    reason: str
    rating: float | None
    name: str | None

class DecisionEngine:
    def decide(self, age: int | None, ai_result: dict, prefs: dict) -> Decision:
        # 1. Age hard filter
        if age and (age < prefs['age']['min'] or age > prefs['age']['max']):
            return Decision("PASS", f"Age {age} out of range", None, None)
        
        # 2. Rating threshold
        rating = ai_result.get("rating")
        if rating < prefs['rating']['min_threshold']:
            return Decision("PASS", f"Rating {rating} below threshold", rating, ai_result.get("name"))
        
        # 3. Body type filter
        if prefs['body_type']['require_slim_athletic'] and not ai_result.get("slim_athletic"):
            return Decision("PASS", "Not slim/athletic", rating, ai_result.get("name"))
        
        # 4. Ethnicity filter
        if prefs['ethnicity']['allowed'] and not ai_result.get("ethnicity_ok"):
            return Decision("PASS", f"Ethnicity not in allowed list", rating, ai_result.get("name"))
        
        # Passed all filters
        return Decision("LIKE", ai_result.get("reason", "All filters passed"), rating, ai_result.get("name"))
```

---

## 3. WhatsApp Integration

### NEW FILE: `src/notifications/whatsapp.py`

```python
class WhatsAppNotifier:
    def __init__(self, group_jid: str):
        self.group_jid = group_jid
    
    def send_like_notification(self, decision: Decision, screenshot_path: str = None):
        message = f"""✅ *LIKED: {decision.name}*
Rating: {decision.rating}/10
Reason: {decision.reason}"""
        
        if screenshot_path:
            self._send_image(screenshot_path, message)
        else:
            self._send_text(message)
    
    def _send_text(self, message: str):
        subprocess.run([
            "openclaw", "message", "send",
            "--channel", "whatsapp",
            "--target", self.group_jid,
            "--message", message
        ], timeout=30)
    
    def _send_image(self, image_path: str, caption: str):
        subprocess.run([
            "openclaw", "message", "send",
            "--channel", "whatsapp",
            "--target", self.group_jid,
            "--media", image_path,
            "--message", caption
        ], timeout=60)
```

---

## 4. ADB Action Execution

### MODIFY: `src/utils/adb_helpers.py`

Add methods from `hinge_gemini_runner.py`:

```python
def find_button_coords(xml: str, pattern: str) -> tuple | None:
    """Find button coordinates by content-desc pattern."""
    # Port from hinge_gemini_runner lines 459-478

def execute_like(xml: str):
    """Tap Like button, send priority like."""
    # Port from lines 686-738

def execute_skip(xml: str):
    """Tap Skip button with verification."""
    # Port from lines 653-683
```

---

## 5. Main Runner

### NEW FILE: `src/runner/auto_swiper.py`

```python
def run_auto_swiper(preferences_path: str, dry_run: bool = False, limit: int = None):
    # Load preferences
    prefs = yaml.safe_load(open(preferences_path))
    
    # Initialize components
    age_extractor = AgeExtractor()
    decision_engine = DecisionEngine()
    notifier = WhatsAppNotifier(prefs['notifications']['whatsapp_group_jid'])
    
    profile_count = 0
    likes = 0
    passes = 0
    
    while True:
        if limit and profile_count >= limit:
            break
        
        # 1. Capture state
        xml = get_ui_dump()
        screenshot = capture_screenshot()
        
        # 2. Extract age (fast)
        age = age_extractor.extract(xml, screenshot)
        
        # 3. Fast age filter
        if age and (age < prefs['age']['min'] or age > prefs['age']['max']):
            execute_skip(xml)
            passes += 1
            continue
        
        # 4. Run expensive AI analysis
        profile_photos = capture_profile_photos()
        ai_result = analyze_profile_simple(profile_photos[0], prefs)  # Simple Gemini call
        
        # 5. Apply decision engine
        decision = decision_engine.decide(age, ai_result, prefs)
        
        # 6. Execute action
        if not dry_run:
            if decision.action == "LIKE":
                execute_like(xml)
                likes += 1
                if prefs['notifications']['notify_on_like']:
                    notifier.send_like_notification(decision, screenshot)
            else:
                execute_skip(xml)
                passes += 1
        
        profile_count += 1
        time.sleep(4)  # Rate limiting
```

---

## 6. Integration with Existing Codebase

### Files to CREATE:
1. `src/extractors/age_extractor.py` - Age extraction cascade
2. `src/decision/engine.py` - Decision logic
3. `src/notifications/whatsapp.py` - WhatsApp messaging
4. `src/runner/auto_swiper.py` - Main loop
5. `src/config/preferences.yaml` - User config
6. `requirements.txt` - Add `pytesseract`

### Files to MODIFY:
1. `src/utils/adb_helpers.py` - Add `execute_like()`, `execute_skip()`, `find_button_coords()`
2. `src/mobile_api/api.py` - Keep existing, but age extraction moves to AgeExtractor
3. `src/demo/demo.py` - Optional: keep for manual testing

### Files to KEEP UNCHANGED:
- `src/algo/feature_extract.py` - Complex DSPy analysis (still valuable)
- `src/models/profile.py` - Data models
- `src/agent/react_agent.py` - DSPy infrastructure

---

## 7. Testing Strategy

### Dry-Run Mode
```bash
python -m src.runner.auto_swiper \
  --config src/config/preferences.yaml \
  --dry-run \
  --limit 10
```

**Behavior:**
- Captures screenshots
- Extracts age
- Runs AI analysis
- Makes decisions
- **Does NOT execute ADB actions**
- **Does NOT send WhatsApp messages**
- Prints decisions to console

### Age Extraction Test
```bash
python -m src.extractors.age_extractor --test
```

Cycles through methods on sample screenshots.

---

## 8. Migration Path

### Phase 1: Age Extraction (Week 1)
- Implement `AgeExtractor` with all 4 methods
- Test on 100 profiles, measure success rate
- Target: >95% age extraction

### Phase 2: Decision Engine (Week 1)
- Implement `DecisionEngine` + preferences
- Add dry-run mode
- Test decision logic without actions

### Phase 3: ADB Actions (Week 2)
- Port like/skip automation from hinge_gemini_runner
- Test on emulator with visual verification
- Add safety checks (duplicate profile detection)

### Phase 4: WhatsApp Integration (Week 2)
- Implement WhatsAppNotifier
- Test notifications (text + images)
- Add error handling

### Phase 5: Production Runner (Week 3)
- Implement main loop with rate limiting
- Add session management (lock files, metrics)
- Production testing

---

## 9. Open Questions for Boss

1. **Simple vs Complex Analysis:**
   - Use simple 1-shot Gemini call (like hinge_gemini_runner)?
   - Or keep complex DSPy multi-photo analysis?
   - **Recommendation:** Start simple, add complexity later if needed

2. **Notification Frequency:**
   - Send WhatsApp for every like? Or batch summaries?
   - **Recommendation:** Individual likes + hourly summary

3. **Rate Limiting:**
   - Gemini free tier: 15 RPM, 1500 RPD
   - How aggressive should swiping be?
   - **Recommendation:** 4-second delay = ~900 profiles/hour max

4. **Error Handling:**
   - What happens if age extraction fails on all 4 methods?
   - Skip profile? Send error notification?
   - **Recommendation:** Skip + log for manual review

5. **Multi-User Support (SaaS):**
   - Each user has own preferences.yaml?
   - Separate WhatsApp groups per user?
   - **Recommendation:** User-specific config files in `configs/user_{id}.yaml`

---

## 10. Dependencies

### New Python Packages
```
pytesseract>=0.3.10
pyyaml>=6.0
```

### System Dependencies
```bash
brew install tesseract  # OCR engine
```

---

## 11. Success Metrics

- **Age Extraction:** >95% success rate
- **Decision Accuracy:** Match Boss's manual decisions >90%
- **Speed:** <6 seconds per profile (including AI call)
- **Reliability:** Run continuously for 6 hours without crashes
- **WhatsApp:** 100% message delivery

---

## Next Steps

1. **Boss reviews this plan** → suggest changes
2. **Spawn implementation agent** with approved plan
3. **Create feature branch** (already done: `feature/auto-decision-whatsapp`)
4. **Implement in phases** (2-3 weeks)
5. **Test on emulator**
6. **Deploy to production**

