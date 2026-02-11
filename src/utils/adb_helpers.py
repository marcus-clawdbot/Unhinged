"""
ADB helper functions for Android device automation.

Includes UI capture, tap/swipe/type primitives, and Hinge-specific actions.
"""

import subprocess
import shlex
import pathlib
import time
import random
import os
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ADB configuration - can be overridden via environment
ADB_PATH = os.environ.get("ADB_PATH", "adb")
ADB_SERIAL = os.environ.get("ADB_SERIAL", "emulator-5554")
HINGE_PKG = "co.hinge.app"


# === Core ADB primitives ===

def _adb_cmd(args: list, timeout: int = 30) -> Optional[str]:
    """Run ADB command and return stdout."""
    cmd = [ADB_PATH, "-s", ADB_SERIAL] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.error(f"ADB timeout: {' '.join(cmd[:4])}")
        return None
    except Exception as e:
        logger.error(f"ADB error: {e}")
        return None


def _adb_cmd_bin(args: list, timeout: int = 30) -> Optional[bytes]:
    """Run ADB command and return binary stdout."""
    cmd = [ADB_PATH, "-s", ADB_SERIAL] + args
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.stdout
    except Exception as e:
        logger.error(f"ADB binary error: {e}")
        return None


def tap(x: int, y: int) -> None:
    """Tap at coordinates."""
    _adb_cmd(["shell", "input", "tap", str(x), str(y)])

# ADB type text primitive (human-like typing)

def type_text(txt: str) -> None:
    """Type text character by character with human-like delays."""
    for char in txt:
        # Use adb to type each character
        quoted_char = shlex.quote(char).replace(' ', '%s')
        subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "shell", "input", "text", quoted_char], check=True)
        # Sleep a random time between 0.05 and 0.25 seconds
        time.sleep(random.uniform(0.05, 0.25))
    # Press BACK to close the keyboard
    subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "shell", "input", "keyevent", "4"], check=True)

# XML bounds parsing

def parse_bounds(bounds_str):
    """Parses bounds string '[x1,y1][x2,y2]' into (x1, y1, x2, y2)."""
    import re
    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if match:
        return tuple(map(int, match.groups()))
    return None

def get_element_center(bounds):
    """Calculates the center (x, y) of a bounds tuple."""
    if bounds:
        x1, y1, x2, y2 = bounds
        return (x1 + x2) // 2, (y1 + y2) // 2
    return None

def get_ui_dump(dump_number: int = 0) -> str:
    """Dumps UI hierarchy from device and pulls it locally to a numbered file in window_dump folder."""
    # Create window_dump directory if it doesn't exist
    dump_dir = "window_dump"
    os.makedirs(dump_dir, exist_ok=True)
    
    time.sleep(0.5)
    remote_path = "/sdcard/window_dump.xml"
    local_path = os.path.join(dump_dir, f"window_dump_{dump_number}.xml")
    
    subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "shell", "uiautomator", "dump", "--compressed", remote_path], check=True)
    subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "pull", remote_path, local_path], check=True)
    logger.debug(f"UI hierarchy saved to: {local_path}")
    return local_path

def screenshot(output_path: str) -> bool:
    """Take a screenshot and save it to the specified path."""
    if not output_path:
        logger.error("No output path provided for screenshot")
        return False
        
    try:
        # Ensure output directory exists
        parent_dir = os.path.dirname(output_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        
        # Take screenshot on device
        remote_path = "/sdcard/temp_screenshot.png"
        subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "shell", "screencap", "-p", remote_path], 
                       check=True, capture_output=True)
        
        # Pull screenshot to local machine
        subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "pull", remote_path, output_path], 
                       check=True, capture_output=True)
        
        # Clean up remote file
        subprocess.run([ADB_PATH, "-s", ADB_SERIAL, "shell", "rm", remote_path], 
                       check=True, capture_output=True)
        
        # Verify the file exists and is not empty
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
        else:
            logger.error(f"Screenshot file not created or empty at {output_path}")
            return False
            
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running adb command: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error taking screenshot: {e}")
        return False


def capture_screenshot_fast() -> Optional[str]:
    """Capture screenshot using exec-out (faster, no temp file)."""
    png_path = "/tmp/hinge_profile.png"
    png_bytes = _adb_cmd_bin(["exec-out", "screencap", "-p"])
    if png_bytes and len(png_bytes) > 1000:
        with open(png_path, "wb") as f:
            f.write(png_bytes)
        return png_path
    return None


# === UI state detection ===

def get_ui_xml() -> Optional[str]:
    """Get UI hierarchy XML as string (faster than file dump)."""
    _adb_cmd(["shell", "uiautomator", "dump", "/sdcard/ui.xml"])
    return _adb_cmd(["shell", "cat", "/sdcard/ui.xml"])


def is_hinge_profile(xml: str) -> bool:
    """Check if we're on a Hinge profile screen (or like modal)."""
    if not xml:
        return False
    if "co.hinge.app" not in xml:
        return False
    # Normal profile view has Skip or Like buttons
    if "Skip" in xml or "Like photo" in xml or "Like prompt" in xml:
        return True
    # Like modal is open (also valid - we're on a profile)
    if "Send priority like" in xml or "Send a Rose" in xml:
        return True
    # Discover tab or other Hinge screens
    if "Discover" in xml or "Standouts" in xml:
        return True
    return False


def is_like_modal_open(xml: str) -> bool:
    """Check if the like modal is currently open."""
    if not xml:
        return False
    return "Send priority like" in xml or "Send a Rose" in xml


def is_safety_center_popup(xml: str) -> bool:
    """Detect Safety Center / safety popups that block profile interactions."""
    if not xml:
        return False
    return ("Safety Center" in xml) or ("Safety" in xml and "Center" in xml)


# === Button finding ===

def find_button_coords(xml: str, pattern: str) -> Optional[Tuple[int, int]]:
    """
    Find button center coordinates by content-desc pattern.
    
    Args:
        xml: UI hierarchy XML
        pattern: Pattern to match in content-desc
        
    Returns:
        (x, y) center coordinates, or None
    """
    if not xml:
        return None

    # For Skip button, look specifically for "Skip <Name>" pattern
    if pattern == "Skip":
        regex = r'content-desc="Skip [A-Z][a-z]+"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
        match = re.search(regex, xml)
        if match:
            left, top, right, bottom = map(int, match.groups())
            return ((left + right) // 2, (top + bottom) // 2)
        return None

    # Generic content-desc matching with bounds
    regex = rf'content-desc="[^"]*{pattern}[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    match = re.search(regex, xml, re.IGNORECASE)
    if match:
        left, top, right, bottom = map(int, match.groups())
        return ((left + right) // 2, (top + bottom) // 2)
    return None


def extract_skip_name(xml: str) -> Optional[str]:
    """Extract profile name from Skip button content-desc."""
    if not xml:
        return None
    m = re.search(r'content-desc="Skip\s+([A-Za-z][A-Za-z\- ]{0,30})"', xml)
    return m.group(1).strip() if m else None


# === Navigation helpers ===

def press_back() -> None:
    """Press back button."""
    _adb_cmd(["shell", "input", "keyevent", "KEYCODE_BACK"])


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    """Perform swipe gesture."""
    _adb_cmd(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])


def scroll_down(amount: int = 1200) -> None:
    """Scroll down by swiping up."""
    swipe(540, 1800, 540, 1800 - amount, 300)


def close_other_apps() -> None:
    """Close apps that might interfere."""
    _adb_cmd(["shell", "am", "force-stop", "com.google.android.apps.photos"])
    _adb_cmd(["shell", "am", "force-stop", "com.android.settings"])
    time.sleep(0.5)


def open_hinge() -> None:
    """Open Hinge app and navigate to Discover tab."""
    logger.info("Opening Hinge...")
    close_other_apps()
    _adb_cmd(["shell", "am", "force-stop", HINGE_PKG])
    time.sleep(1)
    _adb_cmd(["shell", "monkey", "-p", HINGE_PKG, "-c", "android.intent.category.LAUNCHER", "1"])
    time.sleep(4)
    # Tap Discover tab (known position)
    tap(108, 2195)
    time.sleep(2)


def scroll_to_top(max_swipes: int = 6) -> None:
    """Best-effort: ensure we're at the top of a profile before acting.

    Hinge profiles can be left mid-scroll; this resets the view so the Like button for the first photo
    is in a predictable location.
    """
    for _ in range(max_swipes):
        # swipe down (content moves down) => user gesture is swipe DOWN on screen
        swipe(540, 700, 540, 2000, 300)
        time.sleep(0.25)


# === Modal handling ===

def close_modal_if_open(xml: str) -> bool:
    """Close the like modal if it's open."""
    if is_like_modal_open(xml):
        logger.debug("Closing open modal...")
        press_back()
        time.sleep(1)
        return True
    return False


def close_safety_center_if_open(xml: str) -> bool:
    """Best-effort close of Safety Center popup."""
    if not is_safety_center_popup(xml):
        return False
    logger.debug("Closing Safety Center popup...")
    press_back()
    time.sleep(1)
    return True


# === Hinge-specific actions ===

def execute_skip(xml: str) -> bool:
    """
    Skip current profile.
    
    Includes verification to avoid double-skipping when tap doesn't register.
    
    Returns:
        True if skip was successful
    """
    # First close any modal that might be open
    if is_like_modal_open(xml):
        logger.debug("Closing modal before skip...")
        press_back()
        time.sleep(1.5)
        xml = get_ui_xml()

    name_before = extract_skip_name(xml)

    coords = find_button_coords(xml, "Skip")
    if coords:
        logger.debug(f"Tapping Skip at {coords}")
        tap(coords[0], coords[1])
    else:
        # Fallback to known position
        logger.debug("Using fallback Skip coords")
        tap(134, 1981)

    time.sleep(1.5)

    # Verify we actually advanced; if not, retry once
    xml_after = get_ui_xml()
    name_after = extract_skip_name(xml_after)
    if name_before and name_after and name_after == name_before:
        logger.warning(f"Skip may not have advanced (still {name_before}). Retrying...")
        coords2 = find_button_coords(xml_after, "Skip")
        if coords2:
            tap(coords2[0], coords2[1])
        else:
            tap(134, 1981)
        time.sleep(1.5)
    
    return True


def execute_like(xml: str, message: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Like current profile using Priority Like.

    We first scroll to the top of the profile, then like the *first photo*.
    This avoids failures when the profile is mid-scroll and the Like button isn't present/visible.

    Args:
        xml: UI hierarchy XML
        message: Optional message to send with like (gigachad message)

    Returns:
        Tuple of (success, message_sent)
    """
    # Ensure we're at the top of the profile so the Like button is predictable.
    scroll_to_top()
    time.sleep(0.5)
    xml = get_ui_xml() or xml

    # Tap "Like photo" button
    coords = find_button_coords(xml, "Like photo")
    if not coords:
        coords = (938, 1347)  # Fallback position (first-photo heart)
        logger.debug(f"Using fallback Like photo coords: {coords}")
    else:
        logger.debug(f"Tapping Like photo at {coords}")

    tap(coords[0], coords[1])
    time.sleep(2)

    # If message provided, type it in the modal
    if message:
        logger.info(f"Adding message: {message}")
        modal_xml = get_ui_xml()
        
        # Find EditText in modal
        edit_match = re.search(
            r'class="android\.widget\.EditText"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            modal_xml or ""
        )
        if edit_match:
            left, top, right, bottom = map(int, edit_match.groups())
            input_coords = ((left + right) // 2, (top + bottom) // 2)
            logger.debug(f"Found input field at {input_coords}")
            tap(input_coords[0], input_coords[1])
            time.sleep(0.5)
        else:
            # Fallback: tap middle area
            logger.debug("Using fallback input coords")
            tap(540, 1400)
            time.sleep(0.5)

        type_text(message)
        time.sleep(1.0)

    # Get fresh UI dump for button coords
    modal_xml = get_ui_xml()

    # Find Send Priority Like button
    priority_coords = find_button_coords(modal_xml, "priority")
    if not priority_coords:
        priority_coords = (655, 1669)  # Fallback position
        logger.debug(f"Using fallback Priority Like coords: {priority_coords}")
    else:
        logger.debug(f"Found Priority Like at {priority_coords}")

    tap(priority_coords[0], priority_coords[1])
    time.sleep(2)

    return True, message


def prime_profile_details() -> None:
    """Small scroll to trigger lazy-loading of profile detail chips without skipping the top bio/prompt area."""
    swipe(540, 1800, 540, 1400, 250)
    time.sleep(0.35)


def scroll_profile_details() -> None:
    """Deeper scroll to bring more profile details into view (used for scraping/capture)."""
    for _ in range(2):
        swipe(540, 1800, 540, 600, 300)
        time.sleep(0.35)