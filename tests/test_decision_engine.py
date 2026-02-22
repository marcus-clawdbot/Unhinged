from src.decision.engine import DecisionEngine, Preferences


def test_age_hard_filter_pass():
    prefs = Preferences(age_min=18, age_max=35)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=40, ai_result={"is_profile": True, "rating": 10, "body_type_score": 8})
    assert d.action == "PASS"


def test_rating_threshold():
    prefs = Preferences(rating_min_threshold=6.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 5, "body_type_score": 8})
    assert d.action == "PASS"


def test_like_when_all_pass():
    prefs = Preferences(rating_min_threshold=6.0, body_type_min_score=5.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type_score": 7})
    assert d.action == "LIKE"


def test_body_type_score_below_threshold():
    prefs = Preferences(body_type_min_score=5.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type_score": 3})
    assert d.action == "PASS"
    assert "Body type score" in d.reason


def test_body_type_score_none_passes():
    """When Gemini returns no body_type_score, don't filter."""
    prefs = Preferences(body_type_min_score=5.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8})
    assert d.action == "LIKE"


def test_body_type_filter_disabled():
    """When min_score is 0, body type filter is disabled."""
    prefs = Preferences(body_type_min_score=0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "body_type_score": 2})
    assert d.action == "LIKE"
