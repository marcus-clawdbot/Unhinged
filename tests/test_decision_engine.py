from src.decision.engine import DecisionEngine, Preferences


def test_age_hard_filter_pass():
    prefs = Preferences(age_min=18, age_max=35)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=40, ai_result={"is_profile": True, "rating": 10, "body_type": "slim_fit"})
    assert d.action == "PASS"


def test_rating_threshold():
    prefs = Preferences(rating_min_threshold=6.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 5, "body_type": "slim_fit"})
    assert d.action == "PASS"


def test_like_when_all_pass():
    prefs = Preferences(rating_min_threshold=6.0, body_type_allowed=["slim_fit", "average"])
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type": "slim_fit"})
    assert d.action == "LIKE"


def test_body_type_heavy_rejected():
    prefs = Preferences(body_type_allowed=["slim_fit", "average"])
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type": "heavy"})
    assert d.action == "PASS"
    assert "heavy" in d.reason


def test_body_type_average_allowed():
    prefs = Preferences(body_type_allowed=["slim_fit", "average"])
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type": "average"})
    assert d.action == "LIKE"


def test_body_type_none_passes():
    """When Gemini returns no body_type, don't filter."""
    prefs = Preferences(body_type_allowed=["slim_fit"])
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8})
    assert d.action == "LIKE"


def test_body_type_filter_disabled():
    """When allowed list is empty, body type filter is disabled."""
    prefs = Preferences(body_type_allowed=[])
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type": "heavy"})
    assert d.action == "LIKE"
