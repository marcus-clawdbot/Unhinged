"""
WhatsApp notification integration via OpenClaw CLI.

Sends like notifications and session summaries to WhatsApp groups.
"""

import subprocess
import logging
import os
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NotificationResult:
    """Result of a notification attempt."""
    success: bool
    error: Optional[str] = None


class WhatsAppNotifier:
    """
    Send notifications to WhatsApp via OpenClaw CLI.
    
    Uses the `openclaw message send` command to send text and images.
    """
    
    def __init__(
        self, 
        group_jid: str,
        enabled: bool = True,
        timeout: int = 30,
    ):
        """
        Initialize the WhatsApp notifier.
        
        Args:
            group_jid: WhatsApp group JID (e.g., "120363424286297551@g.us")
            enabled: Whether notifications are enabled
            timeout: Command timeout in seconds
        """
        self.group_jid = group_jid
        self.enabled = enabled
        self.timeout = timeout
    
    def send_like_notification(
        self,
        name: Optional[str] = None,
        rating: Optional[float] = None,
        reason: Optional[str] = None,
        age: Optional[int] = None,
        screenshot_path: Optional[str] = None,
        message: Optional[str] = None,
        ai_result: Optional[dict] = None,
    ) -> NotificationResult:
        """
        Send a like notification.

        Args:
            name: Profile name
            rating: Profile rating (1-10)
            reason: Reason for like
            age: Profile age
            screenshot_path: Optional screenshot to attach
            message: Optional gigachad message that was sent
            ai_result: Full Gemini analysis dict

        Returns:
            NotificationResult
        """
        if not self.enabled:
            logger.debug("Notifications disabled, skipping")
            return NotificationResult(success=True)

        # Build notification message
        parts = ["✅ *LIKED"]
        if name:
            parts[0] += f": {name}*"
        else:
            parts[0] += "*"

        if age:
            parts.append(f"Age: {age}")

        # Include all Gemini output
        if ai_result:
            for key in ("rating", "body_type", "ethnicity", "vibe", "reason", "red_flags"):
                val = ai_result.get(key)
                if val is not None:
                    if key == "rating":
                        parts.append(f"Rating: {val}/10")
                    elif key == "red_flags" and isinstance(val, list) and val:
                        parts.append(f"Red flags: {', '.join(str(f) for f in val)}")
                    elif key != "red_flags":
                        parts.append(f"{key.replace('_', ' ').title()}: {val}")
        else:
            if rating is not None:
                parts.append(f"Rating: {rating}/10")
            if reason:
                parts.append(f"Reason: {reason}")

        if message:
            parts.append(f"Message: _{message}_")

        text = "\n".join(parts)
        
        # Send with or without image
        if screenshot_path and os.path.exists(screenshot_path):
            return self._send_image(screenshot_path, text)
        else:
            return self._send_text(text)
    
    def send_pass_notification(
        self,
        name: Optional[str] = None,
        reason: Optional[str] = None,
        age: Optional[int] = None,
    ) -> NotificationResult:
        """
        Send a pass notification (usually disabled to avoid spam).
        
        Args:
            name: Profile name
            reason: Reason for pass
            age: Profile age
            
        Returns:
            NotificationResult
        """
        if not self.enabled:
            return NotificationResult(success=True)
        
        parts = ["❌ *PASSED"]
        if name:
            parts[0] += f": {name}*"
        else:
            parts[0] += "*"
        
        if age:
            parts.append(f"Age: {age}")
        if reason:
            parts.append(f"Reason: {reason}")
        
        text = "\n".join(parts)
        return self._send_text(text)
    
    def send_session_summary(
        self,
        profiles_processed: int,
        likes: int,
        passes: int,
        duration_minutes: Optional[float] = None,
        errors: int = 0,
    ) -> NotificationResult:
        """
        Send session summary.
        
        Args:
            profiles_processed: Total profiles processed
            likes: Number of likes
            passes: Number of passes
            duration_minutes: Session duration
            errors: Number of errors
            
        Returns:
            NotificationResult
        """
        if not self.enabled:
            return NotificationResult(success=True)
        
        parts = ["📊 *Hinge Session Complete*"]
        parts.append(f"Profiles: {profiles_processed}")
        parts.append(f"Likes: {likes}")
        parts.append(f"Passes: {passes}")
        
        if duration_minutes:
            parts.append(f"Duration: {duration_minutes:.1f} min")
        
        if errors > 0:
            parts.append(f"Errors: {errors}")
        
        # Like rate
        if profiles_processed > 0:
            like_rate = (likes / profiles_processed) * 100
            parts.append(f"Like rate: {like_rate:.1f}%")
        
        text = "\n".join(parts)
        return self._send_text(text)
    
    def send_error_notification(
        self,
        error_msg: str,
        context: Optional[str] = None,
    ) -> NotificationResult:
        """
        Send error notification.
        
        Args:
            error_msg: Error message
            context: Additional context
            
        Returns:
            NotificationResult
        """
        if not self.enabled:
            return NotificationResult(success=True)
        
        parts = ["⚠️ *Hinge Error*"]
        parts.append(error_msg[:200])  # Limit length
        
        if context:
            parts.append(f"Context: {context}")
        
        text = "\n".join(parts)
        return self._send_text(text)
    
    def _send_text(self, message: str) -> NotificationResult:
        """Send text message via OpenClaw CLI."""
        try:
            result = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "whatsapp",
                    "--target", self.group_jid,
                    "--message", message,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            if result.returncode == 0:
                logger.info(f"WhatsApp sent: {message[:50]}...")
                return NotificationResult(success=True)
            else:
                error = result.stderr[:200] if result.stderr else "Unknown error"
                logger.error(f"WhatsApp send failed: {error}")
                return NotificationResult(success=False, error=error)
                
        except subprocess.TimeoutExpired:
            logger.error("WhatsApp send timed out")
            return NotificationResult(success=False, error="Timeout")
        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            return NotificationResult(success=False, error=str(e))
    
    def _send_image(self, image_path: str, caption: str) -> NotificationResult:
        """Send image with caption via OpenClaw CLI."""
        try:
            result = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "whatsapp",
                    "--target", self.group_jid,
                    "--media", image_path,
                    "--message", caption,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout * 2,  # Images take longer
            )
            
            if result.returncode == 0:
                logger.info(f"WhatsApp image sent: {caption[:30]}...")
                return NotificationResult(success=True)
            else:
                error = result.stderr[:200] if result.stderr else "Unknown error"
                logger.error(f"WhatsApp image send failed: {error}")
                return NotificationResult(success=False, error=error)
                
        except subprocess.TimeoutExpired:
            logger.error("WhatsApp image send timed out")
            return NotificationResult(success=False, error="Timeout")
        except Exception as e:
            logger.error(f"WhatsApp image error: {e}")
            return NotificationResult(success=False, error=str(e))


# Convenience function
def create_notifier_from_preferences(prefs: dict) -> WhatsAppNotifier:
    """Create WhatsAppNotifier from preferences dict."""
    notif = prefs.get("notifications", {})
    return WhatsAppNotifier(
        group_jid=notif.get("whatsapp_group_jid", ""),
        enabled=bool(notif.get("notify_on_like", True)),
    )
