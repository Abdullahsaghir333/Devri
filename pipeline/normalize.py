"""Deterministic ticket normalization (no LLM)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_text_for_model(subject: str, message: str) -> str:
    """Deterministic merge of subject and message for model input."""
    subject_clean = _collapse_whitespace(subject)
    message_clean = _collapse_whitespace(message)
    return f"Subject: {subject_clean}\n\nMessage: {message_clean}"


def normalize_ticket(raw: dict[str, Any]) -> dict[str, Any]:
    subject = str(raw.get("subject", ""))
    message = str(raw.get("message", ""))
    text_for_model = build_text_for_model(subject, message)
    return {
        "ticket_id": str(raw["ticket_id"]),
        "subject": subject,
        "message": message,
        "channel": str(raw.get("channel", "")),
        "created_at": str(raw.get("created_at", "")),
        "text_for_model": text_for_model,
        "char_count": len(text_for_model),
    }


def normalize_tickets(
    tickets: list[dict[str, Any]], output_path: Path
) -> list[dict[str, Any]]:
    normalized = [normalize_ticket(t) for t in tickets]
    output_path.write_text(
        json.dumps(normalized, indent=2) + "\n", encoding="utf-8"
    )
    return normalized
