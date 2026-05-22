"""Final agent queue and markdown summary."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_final_queue(
    predictions: list[dict[str, Any]], overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    override_ids = {o["ticket_id"] for o in overrides}
    queue: list[dict[str, Any]] = []
    for p in predictions:
        queue.append(
            {
                "ticket_id": p["ticket_id"],
                "final_category": p["category"],
                "final_priority": p["priority"],
                "final_route_to": p["route_to"],
                "suggested_reply": p["suggested_reply"],
                "was_overridden": p["ticket_id"] in override_ids,
            }
        )
    queue.sort(key=lambda x: x["ticket_id"])
    return queue


def build_escalations(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    escalations: list[dict[str, Any]] = []
    for p in predictions:
        reasons: list[str] = []
        if p.get("category") == "other":
            reasons.append('category is "other"')
        confidence = float(p.get("confidence", 1.0))
        if confidence < 0.60:
            reasons.append(f"confidence {confidence} below 0.60")
        if reasons:
            escalations.append(
                {
                    "ticket_id": p["ticket_id"],
                    "category": p["category"],
                    "priority": p["priority"],
                    "confidence": confidence,
                    "route_to": p["route_to"],
                    "escalation_reasons": reasons,
                }
            )
    return escalations


def write_queue_summary(queue: list[dict[str, Any]], path: Path) -> None:
    total = len(queue)
    by_category = Counter(item["final_category"] for item in queue)
    by_priority = Counter(item["final_priority"] for item in queue)
    by_route = Counter(item["final_route_to"] for item in queue)
    overridden = [item for item in queue if item["was_overridden"]]

    lines = [
        "# Support Queue Summary",
        "",
        f"**Total tickets:** {total}",
        "",
        "## Count by final category",
        "",
    ]
    for cat, count in sorted(by_category.items()):
        lines.append(f"- {cat}: {count}")
    lines.extend(["", "## Count by final priority", ""])
    for pri, count in sorted(by_priority.items()):
        lines.append(f"- {pri}: {count}")
    lines.extend(["", "## Queue breakdown by destination", ""])
    for route, count in sorted(by_route.items()):
        lines.append(f"- {route}: {count}")
    lines.extend(["", "## Overridden tickets", ""])
    if overridden:
        for item in overridden:
            lines.append(
                f"- {item['ticket_id']}: {item['final_category']} / "
                f"{item['final_priority']} -> {item['final_route_to']}"
            )
    else:
        lines.append("- None")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def save_json(data: Any, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
