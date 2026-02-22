import dspy
from typing import Any, Optional
import lxml.etree as ET
from src.utils.adb_helpers import tap, parse_bounds, get_element_center, type_text, get_ui_dump, screenshot
from PIL import Image
import os
import time
from datetime import datetime

class ProfileInfo:
    """Class to hold profile information that is unique to the current page."""
    name: str
    age: Optional[int]
    location: Optional[str]
    university: Optional[str]
    hometown: Optional[str]
    relationship_type: Optional[str]
    gender: Optional[str]
    height: Optional[str]
    job: Optional[str]
    religion: Optional[str]
    politics: Optional[str]
    prompts: list[str]  # Store prompts and their responses

    def __init__(self):
        self.name = ""
        self.age = None
        self.location = None
        self.university = None
        self.hometown = None
        self.relationship_type = None
        self.gender = None
        self.height = None
        self.job = None
        self.religion = None
        self.politics = None
        self.prompts = []

    def __str__(self):
        info_parts = []
        if self.name:
            info_parts.append(f"Name: {self.name}")
        if self.age:
            info_parts.append(f"Age: {self.age}")
        if self.height:
            info_parts.append(f"Height: {self.height}")
        if self.location:
            info_parts.append(f"Location: {self.location}")
        if self.job:
            info_parts.append(f"Job: {self.job}")
        if self.university:
            info_parts.append(f"University: {self.university}")
        if self.hometown:
            info_parts.append(f"Hometown: {self.hometown}")
        if self.relationship_type:
            info_parts.append(f"Looking for: {self.relationship_type}")
        if self.gender:
            info_parts.append(f"Gender: {self.gender}")
        if self.religion:
            info_parts.append(f"Religion: {self.religion}")
        if self.politics:
            info_parts.append(f"Politics: {self.politics}")
        if self.prompts:
            info_parts.append("\nPrompts:")
            for prompt in self.prompts:
                info_parts.append(f"- {prompt}")
        return "\n".join(info_parts) if info_parts else "No profile information available"

class SubjectPair:
    subject_id: str
    subject_content: str | dspy.Image
    heart_button_bounds: Any
    bounds: tuple  # Store the bounds for screenshot purposes

    def __init__(self, subject_id, subject_content, heart_button_bounds, bounds):
        self.subject_id = subject_id
        self.subject_content = subject_content
        self.heart_button_bounds = heart_button_bounds
        self.bounds = bounds

    def __str__(self):
        if isinstance(self.subject_content, dspy.Image):
            return f"[Image] {self.subject_id}"
        return f"[Text] {self.subject_content}"

class HingeAPI:
    def __init__(self, xml_path="window_dump.xml"):
        self.xml_path = xml_path
        self.profile_info = ProfileInfo()  # Initialize empty profile
        self._update_profile_info()  # First update
        self.subject_pairs = self._parse_subjects_and_hearts()

    def _update_profile_info(self) -> None:
        """Update profile information from the UI hierarchy, preserving existing values.

        Hinge UI structure (as of 2026):
        - Name: in content-desc like "Skip Hannah" or "Hannah's photo"
        - Detail chips: View with content-desc label (e.g. "Age") + sibling TextView with value
        - Prompts: content-desc like "Prompt: Typical Sunday. Answer: Chilling and cuddling"
        - Religion/other unlabelled: standalone TextViews (e.g. "Christian")
        """
        import re

        tree = ET.parse(self.xml_path)
        root = tree.getroot()

        # 1. Extract name from "Skip {Name}" button content-desc
        for node in root.iter("node"):
            desc = node.get("content-desc", "")
            m = re.match(r"Skip\s+([A-Za-z][A-Za-z\- ]{0,30})", desc)
            if m:
                self.profile_info.name = m.group(1).strip()
                break

        # 2. Extract labelled detail chips: View(content-desc=label) + sibling TextView(text=value)
        #    Pattern: parent View > child View(content-desc="Age") + child TextView(text="24")
        _label_map = {
            "age": "_age",
            "height": "height",
            "location": "location",
            "job": "job",
            "college or university": "university",
            "home town": "hometown",
            "dating intentions": "relationship_type",
            "gender": "gender",
            "sexuality": "_sexuality",
            "religion": "religion",
            "politics": "politics",
        }

        for node in root.iter("node"):
            desc = (node.get("content-desc") or "").strip()
            if not desc:
                continue
            desc_lower = desc.lower()

            # Match against known labels
            matched_field = None
            for label_key, field_name in _label_map.items():
                if desc_lower == label_key:
                    matched_field = field_name
                    break

            if matched_field:
                # Find the sibling TextView with the value
                parent = node.getparent()
                if parent is None:
                    continue
                for sibling in parent:
                    if sibling is node:
                        continue
                    value = (sibling.get("text") or "").strip()
                    if value:
                        if matched_field == "_age" and value.isdigit():
                            self.profile_info.age = int(value)
                        elif matched_field == "_sexuality":
                            pass  # Not stored in ProfileInfo currently
                        elif matched_field == "gender":
                            self.profile_info.gender = value.lower()
                        elif hasattr(self.profile_info, matched_field):
                            setattr(self.profile_info, matched_field, value)
                        break

        # 3. Extract prompts from content-desc: "Prompt: X. Answer: Y"
        for node in root.iter("node"):
            desc = node.get("content-desc", "")
            m = re.match(r"Prompt:\s*(.+?)\.\s*Answer:\s*(.+)", desc)
            if m:
                prompt = f"{m.group(1).strip()} | {m.group(2).strip()}"
                if prompt not in self.profile_info.prompts:
                    self.profile_info.prompts.append(prompt)

        # 4. Extract religion from unlabelled standalone TextViews in the detail chip area
        #    These appear as TextViews without a labelled sibling (e.g. "Christian")
        #    Only set if not already found via a labelled chip
        if not self.profile_info.religion:
            _known_religions = {
                "christian", "catholic", "muslim", "jewish", "hindu",
                "buddhist", "sikh", "agnostic", "atheist", "spiritual",
            }
            for node in root.iter("node"):
                if node.get("class") == "android.widget.TextView":
                    text = (node.get("text") or "").strip()
                    if text.lower() in _known_religions:
                        self.profile_info.religion = text
                        break

        print(f"\n[PROFILE] {self.profile_info}")

    def _extract_profile_info(self) -> ProfileInfo:
        """Extract profile information from the UI hierarchy."""
        self._update_profile_info()
        return self.profile_info

    def _parse_subjects_and_hearts(self):
        tree = ET.parse(self.xml_path)
        root = tree.getroot()
        subject_pairs = []

        # First, find all view containers that might be cards
        card_containers = []
        for node in root.iter("node"):
            class_name = node.get("class", "")
            if (class_name == "android.view.View" and 
                "bounds" in node.attrib):
                card_containers.append(node)

        # For each card container, extract its content
        for card in card_containers:
            card_bounds = parse_bounds(card.get("bounds"))
            if not card_bounds:
                continue

            # Find all text nodes within this card
            card_texts = []
            for text_node in card.iter("node"):
                text = text_node.get("text", "").strip()
                if text:
                    card_texts.append(text)

            # Find photo nodes within this card
            photo_nodes = []
            for photo_node in card.iter("node"):
                content_desc = photo_node.get("content-desc", "").lower()
                if "photo" in content_desc or "image" in content_desc:
                    photo_bounds = parse_bounds(photo_node.get("bounds"))
                    if photo_bounds:
                        photo_nodes.append((photo_node, content_desc, photo_bounds))

            # Find the closest like button to this card
            card_center = get_element_center(card_bounds)
            min_dist = float("inf")
            closest_like = None

            for node in root.iter("node"):
                if (node.get("class") == "android.widget.Button" and 
                    node.get("content-desc") == "Like"):
                    like_bounds = parse_bounds(node.get("bounds"))
                    if like_bounds:
                        like_center = get_element_center(like_bounds)
                        dist = abs(card_center[1] - like_center[1])
                        if dist < min_dist:
                            min_dist = dist
                            closest_like = like_bounds

            # Create subject pairs for both text and photos
            if card_texts:
                text_content = " | ".join(card_texts)
                subject_id = f"text:{card_bounds}"
                subject_pairs.append(SubjectPair(subject_id, text_content, closest_like, card_bounds))

            for photo_node, photo_desc, photo_bounds in photo_nodes:
                subject_id = f"{photo_desc}:{photo_bounds}"
                subject_pairs.append(SubjectPair(subject_id, photo_desc, closest_like, photo_bounds))

        return subject_pairs

    def get_all_subjects(self):
        """Returns a list of all subjects with their content."""
        return [(str(pair), pair.subject_content, pair.bounds) for pair in self.subject_pairs]

    def get_profile_info(self) -> ProfileInfo:
        """Returns the profile information for the current page."""
        return self.profile_info

    def submit_reply(self, subject_id: str, response_text: str):
        for pair in self.subject_pairs:
            if pair.subject_id == subject_id:
                bounds = pair.heart_button_bounds
                x1, y1, x2, y2 = bounds
                # Tap the heart button (as before)
                center = get_element_center(bounds)
                tap(*center)
                time.sleep(1.5)
                # Get new UI dump after heart tap
                get_ui_dump("window_dump_after_heart.xml")
                tree = ET.parse("window_dump_after_heart.xml")
                root = tree.getroot()
                # Find the input field (EditText)
                input_field = None
                for node in root.iter("node"):
                    class_name = node.get("class", "")
                    if "EditText" in class_name:
                        input_field = node
                        break
                if input_field is not None:
                    input_bounds = parse_bounds(input_field.get("bounds"))
                    if input_bounds:
                        input_center = get_element_center(input_bounds)
                        print(f"Tapping input field at: {input_center}")
                        tap(*input_center)
                        time.sleep(0.5)
                        print(f"Typing response: {response_text}")
                        type_text(response_text)
                        return True
                    else:
                        print("Could not parse input field bounds.")
                else:
                    print("Input field not found after heart tap.")
                return False
        return False

    def capture_subject_photo(self, subject_pair: SubjectPair, output_dir: str = "photo_dump") -> Optional[str]:
        """Capture and save a photo of the subject."""
        if not subject_pair.bounds:
            print("No bounds available for photo capture")
            return None
            
        try:
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            
            # Take full screenshot
            temp_screenshot = os.path.join(output_dir, "temp_screenshot.png")
            if not screenshot(temp_screenshot):
                print("Failed to take screenshot")
                return None
                
            # Crop to subject bounds
            with Image.open(temp_screenshot) as img:
                x1, y1, x2, y2 = subject_pair.bounds
                cropped = img.crop((x1, y1, x2, y2))
                
                # Generate output filename with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_filename = f"photo_{timestamp}.png"
                output_path = os.path.join(output_dir, output_filename)
                
                # Save cropped image
                cropped.save(output_path)
                
            # Clean up temp file
            if os.path.exists(temp_screenshot):
                os.remove(temp_screenshot)
                
            return output_path
            
        except Exception as e:
            print(f"Error capturing photo: {e}")
            return None

    
    
    
