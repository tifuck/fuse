from __future__ import annotations

import re

from .engine import MAX_QUERY, infer_recency, sanitize_query

_VS = re.compile(r"\s+(?:vs\.?|versus)\s+", re.I)
_COMPARE = re.compile(r"^\s*compare\s+", re.I)


def expand_user_question(text: str) -> tuple[list[str], int | None]:
    raw = sanitize_query(text)
    recency = infer_recency(raw, None)
    queries: list[str] = []
    if raw:
        queries.append(raw[:MAX_QUERY])
    body = _COMPARE.sub("", raw).strip()
    parts = [p.strip(" ,.") for p in _VS.split(body) if p.strip(" ,.")]
    if len(parts) >= 2:
        for part in parts[:4]:
            if part and part.lower() not in {q.lower() for q in queries}:
                queries.append(part[:MAX_QUERY])
    elif _COMPARE.match(raw):
        bits = [b.strip() for b in re.split(r"\s+and\s+|,", body) if b.strip()]
        for bit in bits[:4]:
            if bit and bit.lower() not in {q.lower() for q in queries}:
                queries.append(bit[:MAX_QUERY])
    return queries[:4], recency
