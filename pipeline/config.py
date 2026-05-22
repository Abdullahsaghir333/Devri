"""Load and validate triage configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def route_for_category(config: dict[str, Any], category: str) -> str:
    rules = config["routing_rules"]
    if category not in rules:
        raise ValueError(f"No routing rule for category: {category}")
    return rules[category]


def validate_category(config: dict[str, Any], category: str) -> str:
    allowed = config["allowed_categories"]
    if category not in allowed:
        raise ValueError(f"Invalid category: {category}")
    return category


def validate_priority(config: dict[str, Any], priority: str) -> str:
    allowed = config["allowed_priorities"]
    if priority not in allowed:
        raise ValueError(f"Invalid priority: {priority}")
    return priority


def word_count(text: str) -> int:
    return len(text.split())
