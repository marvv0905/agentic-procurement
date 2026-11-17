"""Pre-download the local embedding model (Lab 3 sentence embeddings).

Run once on a machine with network access so the procurement pipeline never
depends on the Hugging Face Hub at runtime (demos, CI, offline runs):

    uv run python scripts/download_embedding_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procurement.embeddings import MODEL_NAME, _encoder


def main() -> None:
    print(f"Downloading {MODEL_NAME} ...")
    encoder = _encoder()
    # A tiny warm-up encode also verifies the install end to end.
    vector = encoder.encode(["降噪 noise cancelling"])
    print(f"Done. Embedding dimension: {len(vector[0])}")


if __name__ == "__main__":
    main()