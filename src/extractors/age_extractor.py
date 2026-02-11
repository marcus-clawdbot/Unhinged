"""
Robust age extraction with 4-method cascade fallback.

Methods in order:
1. XML label matching (content-desc="Age")
2. Header positional parsing (from hinge_gemini_runner)
3. OCR fallback (pytesseract bands)
4. AI vision result (passed from caller)
"""

import re
import os
import json
import subprocess
import logging
from typing import Optional
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


class AgeExtractor:
    """Extract age from Hinge profiles using a cascade of methods."""
    
    # OCR bands for multi-band scanning
    OCR_BANDS = [
        {"start": 0.0, "end": 0.22},
        {"start": 0.22, "end": 0.55},
        {"start": 0.55, "end": 0.9},
    ]
    
    # Regex patterns
    AGE_RE = re.compile(r"(?<!\d)(?:1[89]|[2-9]\d)(?!\d)")
    HEIGHT_FT_RE = re.compile(r"(?i)\b([4-7])\s*(?:'|ft)\s*([0-9]|1[01])\s*(?:\"|in)?\b")
    HEIGHT_CM_RE = re.compile(r"(?i)(1[4-9]\d|2[0-2]\d)\s*cm")

    def extract(
        self, 
        xml_dump: str, 
        screenshot_path: Optional[str] = None, 
        ai_result: Optional[dict] = None
    ) -> Optional[int]:
        """
        Extract age using cascade of methods.
        
        Args:
            xml_dump: UI hierarchy XML string
            screenshot_path: Path to screenshot for OCR fallback
            ai_result: Optional AI vision analysis result with 'age' key
            
        Returns:
            Age as int, or None if not found
        """
        # Method 1: XML label matching
        age = self._extract_from_labels(xml_dump)
        if age:
            logger.debug(f"Age from XML labels: {age}")
            return age
        
        # Method 2: Header positional parsing
        age = self._extract_from_header(xml_dump)
        if age:
            logger.debug(f"Age from header parsing: {age}")
            return age
        
        # Method 3: OCR fallback
        if screenshot_path and os.path.exists(screenshot_path):
            age = self._extract_from_ocr(screenshot_path)
            if age:
                logger.debug(f"Age from OCR: {age}")
                return age
        
        # Method 4: AI vision result
        if ai_result and ai_result.get("age"):
            age = ai_result.get("age")
            try:
                age = int(age)
                logger.debug(f"Age from AI: {age}")
                return age
            except (ValueError, TypeError):
                pass
        
        logger.warning("Age extraction failed on all methods")
        return None

    def _extract_from_labels(self, xml_dump: str) -> Optional[int]:
        """
        Extract age from labeled XML elements (content-desc="Age").
        
        This looks for elements with "age" in content-desc and finds
        associated text values.
        """
        if not xml_dump:
            return None
            
        try:
            root = ET.fromstring(xml_dump.encode("utf-8") if isinstance(xml_dump, str) else xml_dump)
        except Exception as e:
            logger.error(f"Failed to parse XML: {e}")
            return None
        
        # Build parent map for traversal
        parent_map = {c: p for p in root.iter() for c in p}
        
        for node in root.iter('node'):
            label = (node.get('content-desc') or '').lower()
            if 'age' not in label:
                continue
            
            # Try to get value from siblings or children
            value = self._gather_text_value(node, parent_map)
            if value and value.isdigit():
                age = int(value)
                if 18 <= age <= 99:
                    return age
        
        return None

    def _gather_text_value(self, node, parent_map, depth: int = 2) -> Optional[str]:
        """Gather text value from node or its siblings."""
        if depth < 0 or node is None:
            return None
        
        # Check text attribute
        text = (node.get('text') or '').strip()
        if text:
            return text
        
        # Check children
        for child in list(node):
            if child.get("class") == "android.widget.TextView":
                value = (child.get("text") or '').strip()
                if value:
                    return value
            result = self._gather_text_value(child, parent_map, depth - 1)
            if result:
                return result
        
        # Check siblings
        parent = parent_map.get(node)
        if parent:
            siblings = list(parent)
            try:
                idx = siblings.index(node)
            except ValueError:
                idx = -1
            for sibling in siblings[idx + 1:]:
                if sibling.get("class") == "android.widget.TextView":
                    value = (sibling.get("text") or '').strip()
                    if value:
                        return value
        
        return None

    def _extract_from_header(self, xml_dump: str) -> Optional[int]:
        """
        Extract age from header using positional parsing.
        
        This method finds the name/header area and parses age from
        nearby text elements based on their screen position.
        Ported from hinge_gemini_runner.py lines 377-431.
        """
        if not xml_dump:
            return None
        
        try:
            root = ET.fromstring(xml_dump.encode("utf-8") if isinstance(xml_dump, str) else xml_dump)
        except Exception as e:
            logger.error(f"Failed to parse XML for header: {e}")
            return None
        
        # Collect text nodes with bounds
        texts = []
        for node in root.iter('node'):
            text_val = (node.get('text') or '').strip()
            desc_val = (node.get('content-desc') or '').strip()
            content = text_val or desc_val
            if not content:
                continue
            
            bounds = self._parse_bounds(node.get('bounds'))
            if not bounds:
                continue
            
            x1, y1, x2, y2 = bounds
            # Filter to top portion of screen (header area)
            if y2 < 0 or y1 > 2100:
                continue
            
            texts.append((y1, x1, content))
        
        if not texts:
            return None
        
        texts.sort()
        
        # Find name candidates (top of screen, contains letters, short)
        name_candidates = [
            t for t in texts 
            if t[0] < 600 and any(c.isalpha() for c in t[2]) and len(t[2]) <= 24
        ]
        name_y = name_candidates[0][0] if name_candidates else texts[0][0]
        
        # Get header texts (within 320px of name)
        header_texts = [t for t in texts if name_y <= t[0] <= name_y + 320]
        
        # Group by lines (vertical proximity)
        lines = []
        for y, x, content in header_texts:
            if not lines or abs(lines[-1]['y'] - y) > 30:
                lines.append({'y': y, 'items': [(x, content)]})
            else:
                lines[-1]['items'].append((x, content))
        
        # Combine line items
        line_strs = []
        for line in lines:
            parts = [c for _, c in sorted(line['items'])]
            line_strs.append((" ".join(parts), line['y']))
        
        # Search for age in lines
        for line, _ in line_strs:
            match = self.AGE_RE.search(line)
            if match:
                return int(match.group(0))
        
        return None

    def _parse_bounds(self, bounds_str: Optional[str]) -> Optional[tuple]:
        """Parse bounds string '[x1,y1][x2,y2]' into tuple."""
        if not bounds_str:
            return None
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
        if match:
            return tuple(map(int, match.groups()))
        return None

    def _extract_from_ocr(self, image_path: str) -> Optional[int]:
        """
        Extract age using OCR on multiple bands of the screenshot.
        
        Ported from hinge_gemini_runner.py lines 117-169.
        Uses pytesseract for OCR.
        """
        logger.debug(f"OCR fallback: scanning {image_path}")
        
        ocr_texts = self._ocr_text_bands(image_path, self.OCR_BANDS)
        if ocr_texts:
            return self._extract_age_from_texts(ocr_texts)
        
        return None

    def _ocr_text_bands(self, image_path: str, bands: list) -> list:
        """Run local OCR on multiple vertical bands."""
        script = r"""
import json, os
from PIL import Image
import pytesseract

bands = json.loads(os.environ.get('BANDS', '[]'))
img = Image.open(os.environ['IMG'])
w, h = img.size
out = []
for b in bands:
    y1 = int(h * b['start'])
    y2 = int(h * b['end'])
    crop = img.crop((0, y1, w, y2))
    out.append(pytesseract.image_to_string(crop) or "")
print(json.dumps(out))
"""
        env = os.environ.copy()
        env["IMG"] = image_path
        env["BANDS"] = json.dumps(bands)
        
        try:
            result = subprocess.run(
                ["python3", "-c", script],
                capture_output=True,
                text=True,
                timeout=45,
                env=env,
            )
            if result.returncode != 0:
                # Common case: pytesseract not installed in the interpreter used by this subprocess.
                err = (result.stderr or "").strip()
                if "No module named 'pytesseract'" in err or "No module named pytesseract" in err:
                    logger.warning("OCR unavailable (pytesseract not installed). Install `pytesseract` + `tesseract` to enable OCR fallback.")
                else:
                    logger.error(f"OCR error: {err[:200]}")
                return []
            
            texts = json.loads(result.stdout.strip() or "[]")
            for idx, t in enumerate(texts):
                preview = (t or "").replace("\n", " ").strip()[:70]
                logger.debug(f"OCR band {idx + 1}/{len(texts)}: {preview or '<empty>'}")
            return texts
            
        except Exception as e:
            logger.error(f"OCR failed: {e}")
            return []

    def _extract_age_from_texts(self, texts: list) -> Optional[int]:
        """Extract age from a list of OCR text strings."""
        for t in texts:
            if not t:
                continue
            match = self.AGE_RE.search(t)
            if match:
                age = int(match.group(0))
                if 18 <= age <= 99:
                    return age
        return None


# Convenience function for standalone testing
def test_age_extractor():
    """Test age extraction on sample data."""
    extractor = AgeExtractor()
    
    # Test with sample XML
    sample_xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <hierarchy>
        <node class="android.view.View" content-desc="Age">
            <node class="android.widget.TextView" text="25"/>
        </node>
    </hierarchy>
    '''
    
    age = extractor.extract(sample_xml)
    print(f"Test XML label extraction: age={age}")
    
    # Test header parsing
    header_xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <hierarchy>
        <node class="android.widget.TextView" text="Sarah" bounds="[100,200][300,250]"/>
        <node class="android.widget.TextView" text="27" bounds="[350,200][400,250]"/>
    </hierarchy>
    '''
    
    age = extractor.extract(header_xml)
    print(f"Test header extraction: age={age}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_age_extractor()
