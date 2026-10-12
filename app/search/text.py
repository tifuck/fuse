"""Tokenizing and lexical relevance shared by search re-ranking and page passages.

No model calls and no index: a light stemmer plus BM25 over the chunks of one
page (or the titles/snippets of one result list) is enough to put the right
section first, and it costs microseconds.
"""
from __future__ import annotations

import math
import re
from collections import Counter

WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
        "has", "have", "how", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or",
        "should", "so", "that", "the", "this", "to", "vs", "was", "what", "when", "where",
        "which", "who", "why", "will", "with", "you", "your",
    }
)
_SUFFIXES = ("ingly", "edly", "ings", "ing", "ies", "ied", "ed", "es", "ly", "s")


def stem(word: str) -> str:
    """Conflate inflections ("restore"/"restoring", "release"/"releases")."""
    w = word.lower().strip("'-")
    if len(w) <= 3 or w.isdigit():
        return w
    for suffix in _SUFFIXES:
        if not w.endswith(suffix) or len(w) - len(suffix) < 3:
            continue
        if suffix == "s" and w.endswith("ss"):
            break
        if suffix == "es" and w[-3] not in "sxzh":
            # "releases" -> "release" (then "releas"), not "releas" + "es"
            w = w[:-1]
            break
        w = w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
        break
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def terms(text: str) -> list[str]:
    """Content-word stems, stop words removed, in order (duplicates kept)."""
    out: list[str] = []
    for word in WORD_RE.findall((text or "").lower()):
        word = word.strip("'-")
        if len(word) < 2 or word in STOP_WORDS:
            continue
        out.append(stem(word))
    return out


def coverage(query_terms: set[str], text: str) -> float:
    """Share of the distinct query terms that occur in text (0..1)."""
    if not query_terms:
        return 0.0
    found = set(terms(text)) & query_terms
    return len(found) / len(query_terms)


def bm25_scores(docs: list[list[str]], query_terms: list[str], *, k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Okapi BM25 of each tokenized doc against the query terms."""
    if not docs or not query_terms:
        return [0.0] * len(docs)
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))
    unique = set(query_terms)
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in unique}
    scores: list[float] = []
    for doc in docs:
        tf = Counter(doc)
        norm = k1 * (1 - b + b * len(doc) / avg)
        score = 0.0
        for t in unique:
            f = tf.get(t, 0)
            if f:
                score += idf[t] * f * (k1 + 1) / (f + norm)
        scores.append(score)
    return scores
