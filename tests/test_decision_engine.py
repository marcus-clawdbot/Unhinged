from src.decision.engine import DecisionEngine, Preferences


def test_age_hard_filter_pass():
    prefs = Preferences(age_min=18, age_max=35)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=40, ai_result={"is_profile": True, "rating": 10, "slim_athletic": True})
    assert d.action == "PASS"


def test_rating_threshold():
    prefs = Preferences(rating_min_threshold=6.0)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 5, "slim_athletic": True})
    assert d.action == "PASS"


def test_like_when_all_pass():
    prefs = Preferences(rating_min_threshold=6.0, require_slim_athletic=True)
    eng = DecisionEngine(preferences=prefs)
    d = eng.decide(age=25, ai_result={"is_profile": True, "rating": 8, "slim_athletic": True})
    assert d.action == "LIKE"
