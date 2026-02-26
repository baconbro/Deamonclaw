"""
Embedding-based salience scorer using sentence-transformers.

Scores perception events by their semantic similarity to a set of
"importance" concept phrases.  Also provides text → embedding for
vector storage in pgvector (used by EpisodicMemory.recall_semantic).

Falls back to keyword-based scoring when sentence-transformers is not
installed, so the rest of the sidecar continues to work in environments
where the model can't be downloaded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Concepts that represent "important" events
_IMPORTANCE_CONCEPTS = [
    "urgent deadline approaching",
    "critical system error",
    "important meeting scheduled",
    "budget decision required",
    "emergency situation",
    "action required immediately",
    "payment overdue",
    "data loss risk",
    "security breach detected",
    "client escalation",
    "outage or downtime",
    "legal or compliance issue",
    "production incident",
    "blocked on task",
]

# Keyword fallback (used when model is unavailable)
_SALIENCE_KEYWORDS = {
    "urgent": 0.20,
    "deadline": 0.18,
    "emergency": 0.22,
    "critical": 0.20,
    "asap": 0.18,
    "important": 0.15,
    "meeting": 0.12,
    "budget": 0.12,
    "payment": 0.14,
    "error": 0.14,
    "breach": 0.20,
    "outage": 0.20,
    "incident": 0.18,
    "escalation": 0.18,
    "blocked": 0.12,
    "overdue": 0.16,
    "legal": 0.15,
    "compliance": 0.15,
    "down": 0.10,
    "loss": 0.12,
}

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False
    logger.warning(
        "sentence-transformers not installed — using keyword-based salience fallback"
    )


class EmbeddingSalienceScorer:
    """
    Semantic salience scorer.

    Usage:
        scorer = EmbeddingSalienceScorer()
        await scorer.initialize()           # loads model in thread pool
        score = scorer.score("urgent Q3 deadline moved up")   # → ~0.85
        vec   = await scorer.embed("important meeting at 2pm")  # → list[float]
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model: Any = None
        self._concept_embeddings: Any = None
        self._initialized = False

    async def initialize(self) -> None:
        """Load the sentence-transformer model (CPU-heavy, runs in thread pool)."""
        if not _ST_AVAILABLE:
            logger.info("EmbeddingSalienceScorer: using keyword fallback (no sentence-transformers)")
            self._initialized = True
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_model)
        self._initialized = True
        logger.info("EmbeddingSalienceScorer: model '%s' ready", self._model_name)

    def _load_model(self) -> None:
        self._model = SentenceTransformer(self._model_name)
        self._concept_embeddings = self._model.encode(
            _IMPORTANCE_CONCEPTS,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

    def score(self, text: str) -> float:
        """Return salience score in [0.0, 1.0]."""
        if not text.strip():
            return 0.0
        if self._model is None or self._concept_embeddings is None:
            return self._keyword_score(text)

        event_emb = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        # Cosine similarities — embeddings are already L2-normalised
        similarities = np.dot(self._concept_embeddings, event_emb.T).flatten()
        max_sim = float(np.max(similarities))

        # Map [0.3, 0.85] → [0.0, 1.0]
        score = (max_sim - 0.30) / (0.85 - 0.30)
        return max(0.0, min(1.0, score))

    async def embed(self, text: str) -> list[float] | None:
        """
        Generate a 384-dim embedding for storage in pgvector.

        Returns None if sentence-transformers is not available.
        Note: pgvector column is declared vector(1536) for OpenAI compatibility.
        When using all-MiniLM-L6-v2 (384 dims) you'll need to either:
          a) Change the column to vector(384), or
          b) Pad to 1536 with zeros (done here for compatibility).
        """
        if self._model is None:
            return None
        loop = asyncio.get_running_loop()

        def _encode() -> list[float]:
            vec = self._model.encode(
                [text],
                normalize_embeddings=True,
                show_progress_bar=False,
            )[0].tolist()
            # Pad to 1536 dims for pgvector column compatibility
            if len(vec) < 1536:
                vec = vec + [0.0] * (1536 - len(vec))
            return vec

        return await loop.run_in_executor(None, _encode)

    @staticmethod
    def _keyword_score(text: str) -> float:
        t = text.lower()
        total = sum(weight for kw, weight in _SALIENCE_KEYWORDS.items() if kw in t)
        return min(total, 1.0)
