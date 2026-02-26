"""
Video sensor — opt-in webcam monitoring with motion detection.

Pipeline:
  OpenCV VideoCapture → frame sampling → motion diff → JPEG thumbnail → event
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.warning("opencv-python not installed — video sensor disabled")


class VideoSensor:
    """
    Samples webcam frames at a configured interval.
    Only emits an event when significant motion is detected,
    keeping CPU and network overhead low.

    Emits events via on_frame_event(description, metadata) callback.
    The metadata includes a base64-encoded JPEG thumbnail.

    This sensor is opt-in: disabled by default in daemon.yaml.
    """

    def __init__(
        self,
        interval_seconds: int = 60,
        device_index: int = 0,
        motion_threshold: float = 0.02,   # Fraction of changed pixels
        thumbnail_width: int = 320,
        thumbnail_height: int = 240,
        on_frame_event: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self._interval = interval_seconds
        self._device_index = device_index
        self._motion_threshold = motion_threshold
        self._thumb_size = (thumbnail_width, thumbnail_height)
        self._on_frame_event = on_frame_event
        self._running = False
        self._prev_gray = None

    async def run(self) -> None:
        self._running = True

        if not _CV2_AVAILABLE:
            logger.warning(
                "VideoSensor: opencv not available; sensor running in no-op mode"
            )
            while self._running:
                await asyncio.sleep(self._interval)
            return

        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            logger.warning(
                "VideoSensor: cannot open camera device %d; sensor disabled",
                self._device_index,
            )
            self._running = False
            return

        logger.info(
            "VideoSensor: camera device %d open (interval=%ds motion_threshold=%.2f)",
            self._device_index,
            self._interval,
            self._motion_threshold,
        )
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, self._capture, cap)
                if result and self._on_frame_event:
                    description, metadata = result
                    await self._on_frame_event(description, metadata)
        finally:
            cap.release()
            logger.info("VideoSensor: camera released")

    def stop(self) -> None:
        self._running = False

    # ── Internal (blocking — runs in thread pool) ──────────────────────────────

    def _capture(self, cap) -> tuple[str, dict] | None:
        """
        Read one frame, compute motion diff against the previous frame.
        Returns None if no significant motion detected.
        """
        ret, frame = cap.read()
        if not ret:
            logger.warning("VideoSensor: frame read failed")
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion_detected = False
        motion_score = 0.0

        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            motion_score = float(np.sum(thresh > 0)) / thresh.size
            motion_detected = motion_score > self._motion_threshold

        self._prev_gray = gray

        if not motion_detected:
            return None

        # Encode thumbnail
        thumb = cv2.resize(frame, self._thumb_size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(
            ".jpg",
            thumb,
            [cv2.IMWRITE_JPEG_QUALITY, 60],
        )
        if not ok:
            return None

        thumbnail_b64 = base64.b64encode(buf.tobytes()).decode()

        return (
            f"Motion detected (score={motion_score:.3f})",
            {
                "motion_score": motion_score,
                "thumbnail_b64": thumbnail_b64,
                "device_index": self._device_index,
            },
        )
