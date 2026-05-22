"""Interactive human review checkpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pipeline.config import route_for_category, validate_category, validate_priority


def _parse_override_line(
    line: str, config: dict[str, Any], predictions_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 3:
        print(
            f"  Ignored invalid line (expected ticket_id,category,priority): {line}",
            file=sys.stderr,
        )
        return None
    ticket_id, new_category, new_priority = parts
    if ticket_id not in predictions_by_id:
        print(f"  Unknown ticket_id: {ticket_id}", file=sys.stderr)
        return None
    pred = predictions_by_id[ticket_id]
    try:
        new_category = validate_category(config, new_category)
        new_priority = validate_priority(config, new_priority)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return None
    return {
        "ticket_id": ticket_id,
        "old_category": pred["category"],
        "new_category": new_category,
        "old_priority": pred["priority"],
        "new_priority": new_priority,
    }


def display_predictions(predictions: list[dict[str, Any]]) -> None:
    print("\n--- Triage predictions (review) ---")
    for p in predictions:
        print(
            f"  {p['ticket_id']}: category={p['category']}, "
            f"priority={p['priority']}, route_to={p['route_to']}"
        )
    print()


def collect_overrides(
    predictions: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    non_interactive: bool = False,
) -> list[dict[str, Any]]:
    predictions_by_id = {p["ticket_id"]: p for p in predictions}
    display_predictions(predictions)

    if non_interactive:
        return []

    print("Enter any overrides as: ticket_id,category,priority")
    print("Press Enter on an empty line when done.\n")

    overrides: list[dict[str, Any]] = []
    seen: set[str] = set()
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        override = _parse_override_line(line, config, predictions_by_id)
        if override is None:
            continue
        tid = override["ticket_id"]
        if tid in seen:
            for i, existing in enumerate(overrides):
                if existing["ticket_id"] == tid:
                    overrides[i] = override
                    break
        else:
            overrides.append(override)
            seen.add(tid)
        print(
            f"  Override recorded: {tid} -> "
            f"{override['new_category']}, {override['new_priority']}"
        )

    return overrides


def save_overrides(overrides: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")


def apply_overrides(
    predictions: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    override_map = {o["ticket_id"]: o for o in overrides}
    reviewed: list[dict[str, Any]] = []
    for pred in predictions:
        item = dict(pred)
        if pred["ticket_id"] in override_map:
            o = override_map[pred["ticket_id"]]
            item["category"] = o["new_category"]
            item["priority"] = o["new_priority"]
            item["route_to"] = route_for_category(config, o["new_category"])
        reviewed.append(item)
    return reviewed
