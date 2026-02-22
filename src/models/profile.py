from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime
from PIL import Image

class DatingStyle(Enum):
    TRADITIONAL = "traditional"
    CASUAL = "casual"
    ADVENTUROUS = "adventurous"
    UNKNOWN = "unknown"

@dataclass
class PhotoAnalysis:
    """Analysis results for a single photo"""
    has_freckles: Optional[bool] = None
    hair_color: Optional[str] = None
    has_piercings: Optional[bool] = None
    makeup_level: Optional[int] = None
    activities: list[str] = None
    location_type: Optional[str] = None  # indoor, outdoor, social, etc.
    style: Optional[str] = None  # casual, formal, sporty, etc.
    
    def __post_init__(self):
        if self.activities is None:
            self.activities = []

@dataclass
class Education:
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    graduation_year: Optional[int] = None

@dataclass
class Profile:
    # Basic attributes
    name: str
    age: int
    location: str
    bio: str
    created_at: datetime
    
    # Photo analysis
    photos: list[PhotoAnalysis] = None
    
    # Education
    education: Optional[list[Education]] = None
    
    # Lifestyle and preferences
    party_frequency: Optional[int] = None  # 1-5 scale
    drug_usage: Optional[int] = None  # 1-5 scale
    dating_style: DatingStyle = DatingStyle.UNKNOWN
    lifestyle: Optional[str] = None
    
    # Inferred attributes
    inferred_interests: list[str] = None
    inferred_personality_traits: list[str] = None
    
    def __post_init__(self):
        if self.photos is None:
            self.photos = []
        if self.inferred_interests is None:
            self.inferred_interests = []
        if self.inferred_personality_traits is None:
            self.inferred_personality_traits = []
    
    @property
    def has_freckles(self) -> Optional[bool]:
        """Aggregate freckles presence across all photos"""
        if not self.photos:
            return None
        return any(photo.has_freckles for photo in self.photos if photo.has_freckles is not None)
    
    @property
    def hair_color(self) -> Optional[str]:
        """Get the most common hair color across photos"""
        if not self.photos:
            return None
        colors = [p.hair_color for p in self.photos if p.hair_color]
        return max(set(colors), key=colors.count) if colors else None
    
    @property
    def has_piercings(self) -> Optional[bool]:
        """Aggregate piercings presence across all photos"""
        if not self.photos:
            return None
        return any(photo.has_piercings for photo in self.photos if photo.has_piercings is not None)
    
    @property
    def makeup_level(self) -> Optional[int]:
        """Get the average makeup level across photos"""
        if not self.photos:
            return None
        levels = [p.makeup_level for p in self.photos if p.makeup_level is not None]
        return round(sum(levels) / len(levels)) if levels else None
    
    def analyze_profile(self) -> dict:
        """
        Analyzes the profile and returns a dictionary of insights.
        This method should be implemented to use NLP or other analysis techniques
        to extract insights from the profile data.
        """
        insights = {
            "dating_style": self._infer_dating_style(),
            "lifestyle": self._infer_lifestyle(),
            "personality_traits": self._infer_personality(),
            "interests": self._infer_interests(),
            "makeup_analysis": self._analyze_makeup(),
            "party_drug_analysis": self._analyze_party_drug_habits(),
            "photo_analysis": self._analyze_photos()
        }
        return insights
    
    def _analyze_photos(self) -> dict:
        """
        Analyzes patterns across all photos.
        """
        if not self.photos:
            return {}
            
        # Aggregate activities across all photos
        all_activities = []
        for photo in self.photos:
            all_activities.extend(photo.activities)
        
        # Get most common locations and styles
        locations = [p.location_type for p in self.photos if p.location_type]
        styles = [p.style for p in self.photos if p.style]
        
        return {
            "common_activities": list(set(all_activities)),
            "preferred_locations": max(set(locations), key=locations.count) if locations else None,
            "common_style": max(set(styles), key=styles.count) if styles else None,
            "photo_count": len(self.photos),
            "has_freckles": self.has_freckles,
            "hair_color": self.hair_color,
            "has_piercings": self.has_piercings,
            "average_makeup_level": self.makeup_level
        }
    
    def _infer_dating_style(self) -> DatingStyle:
        """
        Infers the dating style based on profile content and photos.
        """
        # Implementation would analyze bio, prompts, and photo content
        return self.dating_style
    
    def _infer_lifestyle(self) -> Optional[str]:
        """
        Infers the lifestyle based on profile content and photos.
        """
        # Implementation would analyze photos, bio, and activity patterns
        return self.lifestyle
    
    def _infer_personality(self) -> list[str]:
        """
        Infers personality traits based on profile content and photos.
        """
        return self.inferred_personality_traits
    
    def _infer_interests(self) -> list[str]:
        """
        Infers interests based on profile content and photos.
        """
        return self.inferred_interests
    
    def _analyze_makeup(self) -> dict:
        """
        Analyzes the amount and style of makeup across all profile photos.
        """
        return {
            "level": self.makeup_level,
            "style": "natural" if self.makeup_level and self.makeup_level <= 2 else "moderate" if self.makeup_level and self.makeup_level <= 4 else "heavy"
        }
    
    def _analyze_party_drug_habits(self) -> dict:
        """
        Analyzes party and drug usage patterns based on photos and profile content.
        """
        return {
            "party_frequency": self.party_frequency,
            "drug_usage": self.drug_usage,
            "risk_level": "low" if (self.party_frequency and self.party_frequency <= 2 and 
                                  self.drug_usage and self.drug_usage <= 2) else "moderate" if (
                                  self.party_frequency and self.party_frequency <= 4 and 
                                  self.drug_usage and self.drug_usage <= 4) else "high"
        }

