"""Semantic similarity via local multilingual sentence embeddings.

Lab 3 applied: sentence embeddings + cosine similarity decide the cases exact
substring matching cannot see — paraphrases ("ANC" vs "active noise
cancellation") and cross-language gaps (降噪 vs "noise cancelling"). The
multilingual model maps both languages into one shared embedding space, so a
Chinese keyword can score against English evidence and vice versa.

Exact substring matching stays the primary check everywhere this module is
used: it is free, explainable, and has no false positives. Embeddings are a
*fallback* (Path A: keyword checks) and a *relevance signal* (Path B: listing
ranking) — never a replacement.

The model (paraphrase-multilingual-mpnet-base-v2) is local, free and compact
(~1 GB). Loading is lazy and cached per process. When the library or the
model weights are unavailable (fresh checkout, CI), every function degrades
gracefully to ``None``/``False`` so callers fall back to substring-only
behavior — a missing model must never break a purchase run.

Implementation notes: a short keyword embedded against one long spec blob
dilutes the cosine similarity, so text is split into sentence-ish chunks and
the score is the MAXIMUM similarity over chunks (the standard trick from the
lab: normalize embeddings, then a single dot product per chunk).
"""

from __future__ import annotations

import re

#: Local, multilingual, free sentence-embedding model (SBERT). mpnet-base is
#: the larger sibling of the lab's MiniLM: measurably better separation for
#: short keyword fragments against spec sentences (~1 GB, local, still free).
MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"

#: Cosine similarity at/above which a semantic hit may count as a PASS.
#: Calibrated on labeled pairs (procurement spec text, both languages):
#: strong matches score 0.60+ (e.g. bluetooth vs 藍牙5.3… = 0.63), while a
#: same-domain unrelated keyword tops out lower (usb transmitter vs Sony
#: headphones = 0.54). The bar is strict because a false PASS would
#: auto-verify a non-compliant product; a weaker hit must stay reviewable.
SEMANTIC_PASS_THRESHOLD = 0.60

#: Below PASS_THRESHOLD but at/above this: possible match, not literally
#: stated — callers report UNKNOWN (the three-valued "never assume" rule
#: extends to the semantic layer: ambiguous hits defer to the human
#: approver, they never silently fail or pass). Cross-domain negatives
#: score well below (standing desk vs headphone specs ≈ 0.17).
SEMANTIC_UNKNOWN_THRESHOLD = 0.30

#: Bounds so a pathological page can never pin CPU/memory: only the first
#: ~4k chars are considered and at most this many chunks are encoded.
_MAX_TEXT_CHARS = 4000
_MAX_CHUNKS = 64

#: Sentence-ish boundaries: Chinese/English sentence enders, newlines,
#: semicolons, and a period followed by whitespace (decimal points stay).
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+|(?<=\.)\s+")
#: Long chunks are further split on list separators (、 ， ,).
_LIST_SPLIT = re.compile(r"[、，,]")
#: Chunks longer than this are split further.
_MAX_CHUNK_CHARS = 80


_encoder_cache: object | None = None
_encoder_failed = False


def _encoder():
    """The lazily loaded SentenceTransformer singleton (cached, never raises).

    A load failure is cached too — repeated attempts on a broken install
    would slow every keyword check.
    """
    global _encoder_cache, _encoder_failed
    if _encoder_cache is not None:
        return _encoder_cache
    if _encoder_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer

        _encoder_cache = SentenceTransformer(MODEL_NAME)
    except Exception:
        _encoder_failed = True
        return None
    return _encoder_cache


def available() -> bool:
    """True when the embedding model is loaded (or loadable) locally."""
    return _encoder() is not None


def _chunks(text: str) -> list[str]:
    """Split text into sentence-ish chunks for max-cosine scoring."""
    text = (text or "").strip()[:_MAX_TEXT_CHARS]
    if not text:
        return []
    parts: list[str] = []
    for part in _SENTENCE_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        if len(part) <= _MAX_CHUNK_CHARS:
            parts.append(part)
        else:
            parts.extend(
                sub.strip() for sub in _LIST_SPLIT.split(part) if sub.strip()
            )
    return parts[:_MAX_CHUNKS] or ([text] if text else [])


def semantic_scores(query: str, texts: list[str]) -> list[float] | None:
    """Cosine similarity of one query against each text (max over chunks).

    Returns ``None`` when the model is unavailable — callers must treat that
    as "no semantic evidence", not as zero similarity.
    """
    encoder = _encoder()
    if encoder is None or not query.strip() or not texts:
        return None
    import numpy as np

    chunks: list[list[str]] = []
    flat: list[str] = []
    for text in texts:
        parts = _chunks(text)
        chunks.append(parts)
        flat.extend(parts)
    if not flat:
        return [0.0] * len(texts)
    # One batch encode: the query, then every chunk of every text.
    embeddings = encoder.encode(
        [query, *flat], normalize_embeddings=True, convert_to_numpy=True
    )
    query_vec = embeddings[0]
    scores: list[float] = []
    offset = 0
    for parts in chunks:
        if not parts:
            scores.append(0.0)
            continue
        vecs = embeddings[1 + offset : 1 + offset + len(parts)]
        offset += len(parts)
        # Normalized embeddings: cosine similarity is a plain dot product.
        scores.append(float(np.dot(vecs, query_vec).max()))
    return scores


def semantic_similarity(query: str, text: str) -> float | None:
    """Cosine similarity of ``query`` against ``text`` (max over chunks)."""
    scores = semantic_scores(query, [text])
    return None if scores is None else scores[0]


def semantic_keyword_scores(
    keywords: list[str], text: str
) -> dict[str, float] | None:
    """Score every keyword against ``text``; the chunks encode only once.

    Returns ``None`` when the model is unavailable.
    """
    encoder = _encoder()
    if encoder is None or not keywords or not text.strip():
        return None
    import numpy as np

    parts = _chunks(text)
    if not parts:
        return {keyword: 0.0 for keyword in keywords}
    queries = [k for k in keywords if k.strip()]
    if not queries:
        return {}
    embeddings = encoder.encode(
        [*queries, *parts], normalize_embeddings=True, convert_to_numpy=True
    )
    keyword_vecs = embeddings[: len(queries)]
    chunk_vecs = embeddings[len(queries) :]
    # Similarity matrix: (keywords x chunks), then max over chunks.
    sim = keyword_vecs @ chunk_vecs.T
    best = sim.max(axis=1) if sim.size else []
    result: dict[str, float] = {}
    for keyword, score in zip(queries, best):
        result[keyword] = float(score)
    # Blank keywords never reach the semantic layer; keep the output shape
    # complete for callers that iterate over the original list.
    for keyword in keywords:
        result.setdefault(keyword, 0.0)
    return result