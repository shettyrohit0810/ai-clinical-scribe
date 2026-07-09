"""ICD-10 candidate retrieval: embed → cosine → top-k.

Embeddings are feature-hashed bag-of-words vectors (256 dims), computed
locally and deterministically:

- Anthropic has no embeddings endpoint, and adding a second vendor (Voyage
  etc.) means another API key and another failure mode. ICD-10 descriptions
  are short, literal clinical phrases ("Pain in right knee"), so token
  overlap IS the semantic signal at this scale — "knee pain" retrieves the
  M25.56x family correctly.
- The storage contract matches the settled design exactly: JSONB float
  arrays + Python cosine. Swapping to a vendor embedding model later is a
  one-function change (embed_text) plus a re-seed; nothing else moves.
- At ~250-300 rows a full scan with Python cosine is microseconds; pgvector
  would add an extension and an index type to defend for zero measurable win.
"""

import hashlib
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IcdCode

EMBEDDING_DIM = 256

# Minimal stopword list: glue words that carry no clinical signal but appear
# in most ICD descriptions ("of", "in", "with", ...).
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it of on or that the to with without unspecified".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def embed_text(text: str) -> list[float]:
    """Hash each token into one of 256 signed buckets, then L2-normalize.
    Deterministic (md5, not Python's salted hash) so seed-time embeddings
    and query-time embeddings always agree across processes."""
    vec = [0.0] * EMBEDDING_DIM
    for token in _tokenize(text):
        digest = hashlib.md5(token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % EMBEDDING_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[index] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    # Vectors are already unit-length, so the dot product IS the cosine.
    return sum(x * y for x, y in zip(a, b))


def rank_candidates(db: Session, query_text: str, k: int = 8) -> list[IcdCode]:
    """Top-k ICD codes for a transcript/query. Full scan + Python cosine —
    see module docstring for why that's the right call at this cardinality."""
    query_vec = embed_text(query_text)
    rows = db.scalars(select(IcdCode).where(IcdCode.embedding.is_not(None))).all()
    scored = sorted(rows, key=lambda r: cosine(query_vec, r.embedding), reverse=True)
    return scored[:k]
