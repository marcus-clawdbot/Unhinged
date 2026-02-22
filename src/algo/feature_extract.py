import dspy
from typing import Optional, Dict, Any
from src.models.profile import Profile, DatingStyle, Lifestyle, Education, PhotoAnalysis
from src.agent.react_agent import ReactAgent
from datetime import datetime
from PIL import Image
import glob
import os
import asyncio
from src.mobile_api.api import ProfileInfo

class InferPhotoFeatures(dspy.Signature):
    """Analyze a single profile photo and extract features."""
    system_prompt: str = dspy.InputField(desc="System prompt for the agent")
    image: dspy.Image = dspy.InputField(desc="Single PIL Image to analyze")
    
    # Physical attributes
    has_freckles: Optional[bool] = dspy.OutputField(desc="Whether the person has freckles")
    hair_color: Optional[str] = dspy.OutputField(desc="Hair color")
    has_piercings: Optional[bool] = dspy.OutputField(desc="Whether the person has piercings")
    makeup_level: Optional[int] = dspy.OutputField(desc="Level of makeup (1-5 scale, 1 being minimal, 5 being heavy)")
    
    # Photo context
    activities: Optional[list[str]] = dspy.OutputField(desc="Activities or actions shown in the photo")
    location_type: Optional[str] = dspy.OutputField(desc="Type of location (indoor, outdoor, social, etc.)")
    style: Optional[str] = dspy.OutputField(desc="Style of the photo (casual, formal, sporty, etc.)")
    
    # Inferred profile information
    bio: Optional[str] = dspy.OutputField(desc="Inferred bio or description based on the photo")
    age: Optional[int] = dspy.OutputField(desc="Estimated age range")
    interests: Optional[list[str]] = dspy.OutputField(desc="Inferred interests from the photo")
    personality_traits: Optional[list[str]] = dspy.OutputField(desc="Inferred personality traits from the photo")


class InferProfileFeatures(dspy.Signature):
    """Aggregate features from all photos and profile data to infer overall profile characteristics."""
    system_prompt: str = dspy.InputField(desc="System prompt for the agent")
    photo_analyses: list[dict] = dspy.InputField(desc="List of photo analysis results")
    profile_info: Dict[str, Any] = dspy.InputField(desc="Profile information from the API as a dictionary")
    
    # Aggregated profile information
    bio: Optional[str] = dspy.OutputField(desc="Combined bio based on all photos and prompts")
    age: Optional[int] = dspy.OutputField(desc="Most likely age based on all photos")
    location: Optional[str] = dspy.OutputField(desc="Inferred location or city")
    job: Optional[str] = dspy.OutputField(desc="Inferred occupation or job")
    
    # Education
    education: Optional[list[dict]] = dspy.OutputField(desc="Inferred education details")
    
    # Lifestyle and preferences
    party_frequency: Optional[int] = dspy.OutputField(desc="Party frequency (1-5 scale)")
    drug_usage: Optional[int] = dspy.OutputField(desc="Drug usage level (1-5 scale)")
    dating_style: Optional[str] = dspy.OutputField(desc="Dating style (traditional, casual, adventurous)")
    lifestyle: Optional[str] = dspy.OutputField(desc="Lifestyle (active, relaxed, party, intellectual)")
    
    # Inferred attributes
    inferred_interests: Optional[list[str]] = dspy.OutputField(desc="Combined list of inferred interests")
    inferred_personality_traits: Optional[list[str]] = dspy.OutputField(desc="Combined list of inferred personality traits")


def load_prompt(prompt_type: str) -> str:
    """Load the appropriate prompt from the prompts file."""
    prompt_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "feature_extractor.txt")
    
    # Default prompts in case file is missing
    default_photo_prompt = """You are an expert at analyzing profile photos and extracting meaningful features. For each photo, analyze:
1. Physical attributes (freckles, hair color, piercings, makeup)
2. Activities or actions shown
3. Location type and setting
4. Style and presentation
5. Inferred personal information (age, interests, personality)

Be objective and thorough in your analysis. If you cannot determine certain attributes with confidence, leave them as null rather than making assumptions."""

    default_profile_prompt = """Analyze this Hinge profile holistically, considering:
1. Patterns across all photos
2. Consistent features and characteristics
3. Overall lifestyle and preferences
4. Education and background

Be thorough but objective in your analysis. Only infer information that you can reasonably determine from the photos. If certain attributes cannot be confidently determined, leave them as null rather than making assumptions."""

    try:
        with open(prompt_file, 'r') as f:
            content = f.read()
        
        if prompt_type == "photo":
            return content.split("# Profile Aggregation Prompt")[0].split("# Individual Photo Analysis Prompt")[1].strip()
        else:
            return content.split("# Profile Aggregation Prompt")[1].strip()
    except (FileNotFoundError, IndexError):
        # Return default prompts if file is missing or malformed
        return default_photo_prompt if prompt_type == "photo" else default_profile_prompt

def map_relationship_to_dating_style(relationship_type: Optional[str]) -> DatingStyle:
    """Map relationship type from the API to a DatingStyle enum value."""
    if not relationship_type:
        return DatingStyle.UNKNOWN
    
    relationship_type = relationship_type.lower()
    
    # Map relationship types to dating styles
    if "long-term" in relationship_type:
        return DatingStyle.TRADITIONAL
    elif "casual" in relationship_type or "short" in relationship_type:
        return DatingStyle.CASUAL
    elif "open" in relationship_type or "poly" in relationship_type:
        return DatingStyle.ADVENTUROUS
    else:
        return DatingStyle.UNKNOWN

async def analyze_profile(
    profile_images: list[str],
    profile_info: ProfileInfo,
) -> Profile:
    """
    Analyze a Hinge profile using both profile images and profile information.
    
    Args:
        profile_images: List of paths to profile images
        profile_info: Profile information from the API
    
    Returns:
        Profile object with analyzed features
    """
    # Load images as PIL Images
    images = [Image.open(path) for path in profile_images]
    
    # Initialize the ReactAgent with our InferPhotoFeatures signature for individual photo analysis
    photo_agent = ReactAgent(
        agent_signature=InferPhotoFeatures,
        model_name="gemini/gemini-2.0-flash"
    )
    
    # Analyze each photo individually
    photo_analyses = []
    for image in images:
        result = await photo_agent.run(
            user_id="",  # No user context needed
            system_prompt=load_prompt("photo"),
            image=image
        )
        photo_analyses.append(result)
    
    # Convert ProfileInfo to dictionary
    profile_dict = {
        "name": profile_info.name,
        "age": profile_info.age,
        "height": profile_info.height,
        "location": profile_info.location,
        "job": profile_info.job,
        "university": profile_info.university,
        "hometown": profile_info.hometown,
        "gender": profile_info.gender,
        "relationship_type": profile_info.relationship_type,
        "religion": profile_info.religion,
        "politics": profile_info.politics,
        "prompts": profile_info.prompts
    }
    
    # Initialize the ReactAgent with our InferProfileFeatures signature for overall analysis
    profile_agent = ReactAgent(
        agent_signature=InferProfileFeatures,
        model_name="gemini/gemini-2.0-flash"
    )
    
    # Run the overall profile analysis
    profile_result = await profile_agent.run(
        user_id="",  # No user context needed
        system_prompt=load_prompt("profile"),
        photo_analyses=photo_analyses,
        profile_info=profile_dict
    )
    
    # Convert photo analyses to PhotoAnalysis objects
    photo_objects = [
        PhotoAnalysis(
            has_freckles=analysis.has_freckles,
            hair_color=analysis.hair_color,
            has_piercings=analysis.has_piercings,
            makeup_level=analysis.makeup_level,
            activities=analysis.activities or [],
            location_type=analysis.location_type,
            style=analysis.style
        )
        for analysis in photo_analyses
    ]
    
    # Convert education data to Education objects
    education_objects = []
    if profile_info.university:
        education_objects.append(Education(
            institution=profile_info.university,
            degree=None,  # Not provided in API
            field=None,   # Not provided in API
            graduation_year=None  # Not provided in API
        ))
    
    # Create and return the Profile object using actual data where available
    return Profile(
        name=profile_info.name or "",
        age=profile_info.age or profile_result.age or 0,
        location=profile_info.location or profile_result.location or "",
        bio=profile_result.bio or "",
        created_at=datetime.now(),
        photos=photo_objects,
        education=education_objects,
        party_frequency=profile_result.party_frequency,
        drug_usage=profile_result.drug_usage,
        dating_style=map_relationship_to_dating_style(profile_info.relationship_type),
        lifestyle=Lifestyle(profile_result.lifestyle.lower()) if profile_result.lifestyle else Lifestyle.UNKNOWN,
        inferred_interests=profile_result.inferred_interests or [],
        inferred_personality_traits=profile_result.inferred_personality_traits or []
    )

async def main():
    # Get all profile photos
    profile_photos = sorted(glob.glob("profile_photos/photo_*.png"))
    
    # Create a mock ProfileInfo for testing
    profile_info = ProfileInfo()
    profile_info.name = "Test User"
    profile_info.age = 25
    profile_info.location = "New York"
    profile_info.university = "NYU"
    profile_info.relationship_type = "Long-term relationship"
    
    # Run analysis
    profile = await analyze_profile(profile_images=profile_photos, profile_info=profile_info)
    
    # Print results
    print("\nProfile Analysis Results:")
    print(f"Name: {profile.name}")
    print(f"Age: {profile.age}")
    print(f"Location: {profile.location}")
    print(f"Bio: {profile.bio}")
    
    print("\nPhoto Analysis:")
    for i, photo in enumerate(profile.photos, 1):
        print(f"\nPhoto {i}:")
        print(f"Location: {photo.location_type}")
        print(f"Style: {photo.style}")
        print(f"Activities: {', '.join(photo.activities)}")
        print(f"Physical Attributes:")
        print(f"  Has Freckles: {photo.has_freckles}")
        print(f"  Hair Color: {photo.hair_color}")
        print(f"  Has Piercings: {photo.has_piercings}")
        print(f"  Makeup Level: {photo.makeup_level}")
    
    print("\nEducation:")
    for edu in profile.education:
        print(f"Institution: {edu.institution}")
        if edu.degree:
            print(f"Degree: {edu.degree}")
        if edu.field:
            print(f"Field: {edu.field}")
    
    print("\nLifestyle:")
    print(f"Party Frequency: {profile.party_frequency}")
    print(f"Drug Usage: {profile.drug_usage}")
    print(f"Dating Style: {profile.dating_style}")
    print(f"Lifestyle: {profile.lifestyle}")
    
    print("\nInferred Interests:", ", ".join(profile.inferred_interests))
    print("Inferred Personality Traits:", ", ".join(profile.inferred_personality_traits))


if __name__ == "__main__":
    asyncio.run(main())
