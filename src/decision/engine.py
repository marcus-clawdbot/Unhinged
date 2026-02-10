"""
Decision engine for auto-swiping.

Applies user preferences to decide LIKE/PASS/SKIP on profiles.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Any
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """Represents a swipe decision."""
    action: str  # "LIKE", "PASS", "SKIP"
    reason: str
    rating: Optional[float] = None
    name: Optional[str] = None
    age: Optional[int] = None
    details: dict = field(default_factory=dict)
    
    def __str__(self) -> str:
        parts = [f"{self.action}"]
        if self.name:
            parts.append(f": {self.name}")
        if self.rating is not None:
            parts.append(f" (rating: {self.rating})")
        if self.reason:
            parts.append(f" - {self.reason}")
        return "".join(parts)


@dataclass  
class Preferences:
    """User preferences for decision engine."""
    # Age range
    age_min: int = 18
    age_max: int = 35
    
    # Rating threshold (1-10 scale)
    rating_min_threshold: float = 6.0
    
    # Body type filter
    require_slim_athletic: bool = True
    
    # Ethnicity filter (None = disabled)
    ethnicity_allowed: Optional[list] = None
    
    # Notification settings
    whatsapp_group_jid: Optional[str] = None
    notify_on_like: bool = True
    notify_on_pass: bool = False
    send_screenshot_with_like: bool = True
    
    # Rate limits
    max_requests_per_minute: int = 15
    max_requests_per_day: int = 1500
    
    @classmethod
    def from_dict(cls, data: dict) -> "Preferences":
        """Create Preferences from a config dict."""
        prefs = cls()
        
        # Age
        if "age" in data:
            prefs.age_min = data["age"].get("min", prefs.age_min)
            prefs.age_max = data["age"].get("max", prefs.age_max)
        
        # Rating
        if "rating" in data:
            prefs.rating_min_threshold = data["rating"].get("min_threshold", prefs.rating_min_threshold)
        
        # Body type
        if "body_type" in data:
            prefs.require_slim_athletic = data["body_type"].get("require_slim_athletic", prefs.require_slim_athletic)
        
        # Ethnicity
        if "ethnicity" in data:
            prefs.ethnicity_allowed = data["ethnicity"].get("allowed")
        
        # Notifications
        if "notifications" in data:
            notif = data["notifications"]
            prefs.whatsapp_group_jid = notif.get("whatsapp_group_jid", prefs.whatsapp_group_jid)
            prefs.notify_on_like = notif.get("notify_on_like", prefs.notify_on_like)
            prefs.notify_on_pass = notif.get("notify_on_pass", prefs.notify_on_pass)
            prefs.send_screenshot_with_like = notif.get("send_screenshot_with_like", prefs.send_screenshot_with_like)
        
        # Rate limits
        if "rate_limits" in data:
            limits = data["rate_limits"]
            prefs.max_requests_per_minute = limits.get("max_requests_per_minute", prefs.max_requests_per_minute)
            prefs.max_requests_per_day = limits.get("max_requests_per_day", prefs.max_requests_per_day)
        
        return prefs

    @classmethod
    def from_yaml(cls, path: str) -> "Preferences":
        """Load preferences from YAML file."""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})


class DecisionEngine:
    """
    Engine that applies user preferences to make swipe decisions.
    
    Decision flow:
    1. Age hard filter (instant PASS if out of range)
    2. Rating threshold check
    3. Body type filter (if enabled)
    4. Ethnicity filter (if enabled)
    5. Red flags check
    """
    
    def __init__(self, preferences: Optional[Preferences] = None):
        """
        Initialize the decision engine.
        
        Args:
            preferences: User preferences, or defaults if None
        """
        self.preferences = preferences or Preferences()
    
    @classmethod
    def from_config(cls, config_path: str) -> "DecisionEngine":
        """Create DecisionEngine from a YAML config file."""
        prefs = Preferences.from_yaml(config_path)
        return cls(prefs)
    
    def decide(
        self, 
        age: Optional[int], 
        ai_result: Optional[dict],
        override_like: bool = False,
    ) -> Decision:
        """
        Make a decision based on age and AI analysis result.
        
        Args:
            age: Profile age (can be None if extraction failed)
            ai_result: AI analysis result dict with keys like:
                - rating: 1-10 score
                - slim_athletic: bool
                - ethnicity_ok: bool
                - name: str
                - reason: str
                - red_flags: list
                - is_profile: bool
            override_like: Force LIKE regardless of filters (aggressive mode)
            
        Returns:
            Decision object with action and reason
        """
        prefs = self.preferences
        
        # Extract name from AI result
        name = ai_result.get("name") if ai_result else None
        rating = ai_result.get("rating") if ai_result else None
        
        # Handle non-profile screens
        if ai_result and not ai_result.get("is_profile", True):
            return Decision(
                action="SKIP",
                reason="Not a profile screen",
                name=name,
                rating=rating,
            )
        
        # 1. Age hard filter
        if age is not None:
            if age < prefs.age_min:
                return Decision(
                    action="PASS",
                    reason=f"Age {age} below minimum ({prefs.age_min})",
                    name=name,
                    rating=rating,
                    age=age,
                )
            if age > prefs.age_max:
                return Decision(
                    action="PASS",
                    reason=f"Age {age} above maximum ({prefs.age_max})",
                    name=name,
                    rating=rating,
                    age=age,
                )
        
        # If no AI result, we can't evaluate further filters
        if not ai_result:
            return Decision(
                action="SKIP",
                reason="No AI analysis available",
                name=name,
                age=age,
            )
        
        # Check override mode
        if override_like:
            return Decision(
                action="LIKE",
                reason="Override mode: force like",
                name=name,
                rating=rating,
                age=age,
            )
        
        # 2. Rating threshold
        if rating is not None:
            try:
                rating_num = float(rating)
                if rating_num < prefs.rating_min_threshold:
                    return Decision(
                        action="PASS",
                        reason=f"Rating {rating_num:.1f} below threshold ({prefs.rating_min_threshold})",
                        name=name,
                        rating=rating_num,
                        age=age,
                    )
            except (ValueError, TypeError):
                logger.warning(f"Invalid rating value: {rating}")
        
        # 3. Body type filter
        if prefs.require_slim_athletic:
            slim_athletic = ai_result.get("slim_athletic", True)  # Default to True if not specified
            if slim_athletic is False:  # Explicit False check
                return Decision(
                    action="PASS",
                    reason="Not slim/athletic body type",
                    name=name,
                    rating=rating,
                    age=age,
                )
        
        # 4. Ethnicity filter (only if allowed list is specified)
        if prefs.ethnicity_allowed:
            ethnicity_ok = ai_result.get("ethnicity_ok", True)
            if ethnicity_ok is False:
                ethnicity = ai_result.get("ethnicity", "unknown")
                return Decision(
                    action="PASS",
                    reason=f"Ethnicity '{ethnicity}' not in allowed list",
                    name=name,
                    rating=rating,
                    age=age,
                )
        
        # 5. Red flags check (optional - just log them)
        red_flags = ai_result.get("red_flags", [])
        if red_flags:
            logger.info(f"Profile has red flags: {red_flags}")
        
        # All filters passed - LIKE!
        reason = ai_result.get("reason", "All filters passed")
        return Decision(
            action="LIKE",
            reason=reason,
            name=name,
            rating=rating,
            age=age,
            details={"red_flags": red_flags} if red_flags else {},
        )

    def quick_age_filter(self, age: Optional[int]) -> bool:
        """
        Quick age check without full decision.
        
        Returns True if age passes filter, False if should skip.
        Used for fast filtering before expensive AI calls.
        """
        if age is None:
            return True  # Can't filter, proceed with analysis
        
        return self.preferences.age_min <= age <= self.preferences.age_max


# Convenience function
def load_preferences(config_path: str) -> Preferences:
    """Load preferences from YAML config file."""
    return Preferences.from_yaml(config_path)
