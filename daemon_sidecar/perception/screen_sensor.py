"""
Screen sensor — periodically captures the display and extracts text via OCR.

Pipeline:
  mss screenshot → PIL resize → pytesseract OCR → text + metadata
  xdotool         → active window title + application name
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

try:
    import mss
    _MSS_AVAILABLE = True
except ImportError:
    _MSS_AVAILABLE = False
    logger.warning("mss not installed — screen sensor disabled")

try:
    from PIL import Image
    import pytesseract
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False
    logger.warning("pytesseract / Pillow not installed — screen sensor disabled")


class ScreenSensor:
    """
    Captures the primary display at a configurable interval and runs OCR
    to extract text content.  Also reads the active window title via xdotool.

    Emits events via on_screen_event(text, metadata) callback.

    Gracefully no-ops when mss / pytesseract / Pillow are not installed.
    """

    def __init__(
        self,
        interval_seconds: int = 30,
        on_screen_event: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self._interval = interval_seconds
        self._on_screen_event = on_screen_event
        self._running = False

    async def run(self) -> None:
        self._running = True

        if not (_MSS_AVAILABLE and _OCR_AVAILABLE):
            logger.warning(
                "ScreenSensor: mss or pytesseract not available; "
                "sensor running in no-op mode"
            )
            while self._running:
                await asyncio.sleep(self._interval)
            return

        logger.info("ScreenSensor: starting (interval=%ds)", self._interval)
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                loop = asyncio.get_running_loop()
                text, metadata = await loop.run_in_executor(None, self._capture)
                if text and self._on_screen_event:
                    await self._on_screen_event(text, metadata)
            except Exception as exc:
                logger.warning("ScreenSensor: capture failed: %s", exc)

    def stop(self) -> None:
        self._running = False

    # ── Internal (blocking — runs in thread pool) ──────────────────────────────

    def _capture(self) -> tuple[str, dict]:
        """Screenshot + OCR + window metadata. Blocking."""
        metadata: dict = {}
        self._populate_window_metadata(metadata)

        with mss.mss() as sct:
            monitor = sct.monitors[1]   # Primary monitor (index 0 is the virtual all-in-one)
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Downscale 50% — OCR is faster and still accurate
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)

        # Tesseract page segmentation mode 6: uniform block of text
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text.strip(), metadata

    @staticmethod
    def _populate_window_metadata(metadata: dict) -> None:
        """Try to read active window info via xdotool (Linux) or osascript (macOS)."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                metadata["active_window"] = result.stdout.strip()
        except FileNotFoundError:
            # xdotool not installed — try wmctrl
            pass
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                metadata["application"] = result.stdout.strip()
        except Exception:
            pass

        # macOS fallback
        if "application" not in metadata:
            try:
                result = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to get name of first process '
                     'whose frontmost is true'],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    metadata["application"] = result.stdout.strip()
            except Exception:
                pass
