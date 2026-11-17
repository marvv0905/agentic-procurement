"""The embedding layer (Lab 3 sentence embeddings) in isolation.

Chunking and graceful degradation are pure/deterministic and always run.
Real multilingual scoring runs only when the model is cached locally
(``scripts/download_embedding_model.py``); on a machine without the weights
those tests are skipped, mirroring the runtime fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from procurement import embeddings


# --- chunking (pure, no model) -------------------------------------------------


def test_chunks_split_on_sentence_enders():
    chunks = embeddings._chunks("主動降噪。藍牙5.3。Battery life 40 hours.")
    assert any("主動降噪" in c for c in chunks)
    assert any("Battery" in c for c in chunks)
    assert all(chunks)


def test_chunks_split_long_lists_on_separators():
    text = "wireless headphones, bluetooth 5.3, active noise cancelling, 40h battery, foldable design"
    chunks = embeddings._chunks(text)
    assert any("noise cancelling" in c for c in chunks)
    assert len(chunks) >= 3


def test_chunks_are_bounded():
    huge = "word " * 5000
    assert len(embeddings._chunks(huge)) <= embeddings._MAX_CHUNKS


def test_chunks_empty_text_is_empty():
    assert embeddings._chunks("") == []
    assert embeddings._chunks("   ") == []


# --- graceful degradation (model unavailable) ----------------------------------


def test_unavailable_model_returns_none(monkeypatch):
    monkeypatch.setattr(embeddings, "_encoder", lambda: None)
    assert embeddings.available() is False
    assert embeddings.semantic_similarity("noise cancelling", "ANC headphones") is None
    assert embeddings.semantic_scores("q", ["a", "b"]) is None
    assert embeddings.semantic_keyword_scores(["k"], "text") is None


def test_load_failure_is_cached(monkeypatch):
    """A broken install fails once and never retries (no per-check stalls)."""
    import types

    monkeypatch.setattr(embeddings, "_encoder_cache", None)
    monkeypatch.setattr(embeddings, "_encoder_failed", False)

    class _Boom:
        def __init__(self, _name):
            raise RuntimeError("broken install")

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _Boom
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

    assert embeddings._encoder() is None
    assert embeddings._encoder_failed is True

    # Even if the loader would now work, the cached failure wins: no retries.
    fake.SentenceTransformer = lambda name: object()
    assert embeddings._encoder() is None
    assert embeddings.available() is False


# --- real multilingual scoring (model must be cached) --------------------------


@pytest.mark.skipif(not embeddings.available(), reason="embedding model not cached")
class TestRealMultilingualScoring:
    def test_cross_language_positive_beats_unrelated(self):
        """降噪 against English noise-cancelling text must score higher than
        an unrelated same-domain keyword against the same text."""
        related = embeddings.semantic_similarity(
            "noise cancelling",
            "Wireless over-ear headphones with active noise cancelling, 40h battery",
        )
        unrelated = embeddings.semantic_similarity(
            "usb transmitter",
            "Wireless over-ear headphones with active noise cancelling, 40h battery",
        )
        assert related is not None and unrelated is not None
        assert related > unrelated

    def test_keyword_scores_batched(self):
        scores = embeddings.semantic_keyword_scores(
            ["noise cancelling", "usb transmitter"],
            "Wireless over-ear headphones with active noise cancelling, Bluetooth 5.3",
        )
        assert scores is not None
        assert scores["noise cancelling"] > scores["usb transmitter"]

    def test_many_texts_scored_consistently(self):
        scores = embeddings.semantic_scores(
            "noise cancelling headphones",
            [
                "Sony WH-1000XM5 wireless noise cancelling headphones",
                "Office chair mesh back",
                "ANC over-ear headphones Bluetooth 5.3",
            ],
        )
        assert scores is not None
        assert scores[0] > scores[1]
        assert scores[2] > scores[1]

    def test_calibration_bands_with_real_model(self):
        """The measured bands that the thresholds were calibrated on."""
        spec = "Wireless over-ear headphones with active noise cancelling, Bluetooth 5.3, 40h battery"
        # Strong cross-language match clears the PASS threshold...
        bluetooth = embeddings.semantic_similarity("bluetooth", "藍牙5.3 無線耳機 主動降噪 40小時電池")
        assert bluetooth is not None and bluetooth >= embeddings.SEMANTIC_PASS_THRESHOLD
        # ...cross-domain negatives stay below the UNKNOWN floor...
        desk = embeddings.semantic_similarity("standing desk", spec)
        assert desk is not None and desk < embeddings.SEMANTIC_UNKNOWN_THRESHOLD
        # ...and same-domain adjacency stays below PASS (never a false PASS).
        usb = embeddings.semantic_similarity("usb transmitter", spec)
        assert usb is not None and usb < embeddings.SEMANTIC_PASS_THRESHOLD