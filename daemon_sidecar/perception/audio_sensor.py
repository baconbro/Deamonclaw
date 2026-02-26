"""
Real audio sensor with Voice Activity Detection and Whisper transcription.

Pipeline:
  Microphone → pyaudio frames → webrtcvad → speech segments → Whisper HTTP → transcription
"""

from __future__ import annotations

import asyncio
import collections
import io
import logging
import wave
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False
    logger.warning("pyaudio not installed — audio sensor will not capture from mic")

try:
    import webrtcvad
    _VAD_AVAILABLE = True
except ImportError:
    _VAD_AVAILABLE = False
    logger.warning("webrtcvad not installed — audio sensor VAD disabled")

# Audio config (webrtcvad requires exactly 10, 20, or 30 ms frames at 8/16/32 kHz)
SAMPLE_RATE = 16_000
CHANNELS = 1
FRAME_DURATION_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)   # 480 samples per frame
BYTES_PER_SAMPLE = 2                                        # int16

# How many consecutive non-speech frames end a segment
PADDING_DURATION_MS = 300
PADDING_FRAMES = PADDING_DURATION_MS // FRAME_DURATION_MS  # 10 frames


class AudioSensor:
    """
    Captures microphone audio, uses VAD to isolate speech segments, and
    transcribes each segment via the faster-whisper HTTP server.

    Emits transcriptions via on_transcription(text, metadata) callback.

    Gracefully no-ops when pyaudio / webrtcvad are not installed —
    the rest of the sidecar continues to work without real audio.
    """

    def __init__(
        self,
        whisper_url: str = "http://localhost:8500",
        vad_aggressiveness: int = 2,
        on_transcription: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self._whisper_url = whisper_url.rstrip("/")
        self._vad_aggressiveness = vad_aggressiveness
        self._on_transcription = on_transcription
        self._running = False

    async def run(self) -> None:
        """Entry point — keeps running until stop() is called."""
        self._running = True

        if not (_PYAUDIO_AVAILABLE and _VAD_AVAILABLE):
            logger.warning(
                "AudioSensor: pyaudio or webrtcvad not available; "
                "sensor running in no-op mode"
            )
            while self._running:
                await asyncio.sleep(60)
            return

        loop = asyncio.get_running_loop()
        # Blocking capture runs in thread pool; send transcription tasks back to the loop
        await loop.run_in_executor(None, self._capture_loop, loop)

    def stop(self) -> None:
        self._running = False

    # ── Internal ───────────────────────────────────────────────────────────────

    def _capture_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Blocking mic capture with VAD.

        Uses a ring buffer of PADDING_FRAMES to detect speech onset / offset:
        - Triggers when >90% of ring buffer is speech
        - Ends when >90% of ring buffer is non-speech
        - Each complete segment is sent to Whisper
        """
        vad = webrtcvad.Vad(self._vad_aggressiveness)
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=FRAME_SIZE,
        )
        ring_buffer: collections.deque[tuple[bytes, bool]] = collections.deque(
            maxlen=PADDING_FRAMES
        )
        triggered = False
        voiced_frames: list[bytes] = []

        logger.info("AudioSensor: mic stream open (rate=%d vad=%d)", SAMPLE_RATE, self._vad_aggressiveness)

        try:
            while self._running:
                raw = stream.read(FRAME_SIZE, exception_on_overflow=False)
                is_speech = vad.is_speech(raw, SAMPLE_RATE)

                if not triggered:
                    ring_buffer.append((raw, is_speech))
                    voiced_ratio = sum(1 for _, s in ring_buffer if s) / max(len(ring_buffer), 1)
                    if voiced_ratio > 0.9:
                        triggered = True
                        voiced_frames.extend(f for f, _ in ring_buffer)
                        ring_buffer.clear()
                else:
                    voiced_frames.append(raw)
                    ring_buffer.append((raw, is_speech))
                    unvoiced_ratio = sum(1 for _, s in ring_buffer if not s) / max(len(ring_buffer), 1)
                    if unvoiced_ratio > 0.9:
                        triggered = False
                        pcm = b"".join(voiced_frames)
                        voiced_frames = []
                        ring_buffer.clear()
                        asyncio.run_coroutine_threadsafe(
                            self._transcribe(pcm),
                            loop,
                        )
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            logger.info("AudioSensor: mic stream closed")

    async def _transcribe(self, pcm: bytes) -> None:
        """Wrap PCM bytes in WAV and send to faster-whisper HTTP server."""
        # Skip very short segments (< 0.5 s)
        if len(pcm) < SAMPLE_RATE * BYTES_PER_SAMPLE * 0.5:
            return

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        wav_buf.seek(0)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._whisper_url}/v1/audio/transcriptions",
                    files={"file": ("audio.wav", wav_buf, "audio/wav")},
                    data={
                        "model": "Systran/faster-whisper-base.en",
                        "response_format": "json",
                    },
                )
                response.raise_for_status()
                text = response.json().get("text", "").strip()

            if text and self._on_transcription:
                logger.debug("AudioSensor: transcribed %d chars", len(text))
                await self._on_transcription(text, {"source": "microphone"})

        except httpx.HTTPError as exc:
            logger.warning("AudioSensor: Whisper HTTP error: %s", exc)
        except Exception as exc:
            logger.warning("AudioSensor: transcription failed: %s", exc)
