"""Redact common credential formats before retaining exception summaries."""

from __future__ import annotations

import re


_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+")
_SECRET_FIELD = re.compile(
    r'''(?i)(\b(?:garmin_token|password|passwd|access_token|refresh_token|id_token|client_secret|private_key|token|cookie|set-cookie|session)\b\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)'''
)


def safe_error(value: object, limit: int = 1000) -> str:
    message = str(value)
    message = _AUTH_HEADER.sub(r"\1[REDACTED]", message)
    message = _SECRET_FIELD.sub(r"\1[REDACTED]", message)
    return message[:limit]
