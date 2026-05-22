"""LLM triage prediction with validation, routing, and batch resilience."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pipeline.config import (
    route_for_category,
    validate_category,
    validate_priority,
    word_count,
)
from pipeline.llm_log import log_llm_call
from pipeline.paths import (
    CONFIG_PATH,
    LLM_LOG_PATH,
    NORMALIZED_PATH,
    PREDICTIONS_PATH,
    RECOVERY_LOG_PATH,
)


def _truncate_reply(reply: str, max_words: int) -> str:
    words = reply.split()
    if len(words) <= max_words:
        return reply.strip()
    return " ".join(words[:max_words]).strip()


def _build_prompt(
    normalized: list[dict[str, Any]], config: dict[str, Any]
) -> str:
    max_words = config["reply_style"]["max_words"]
    tone = config["reply_style"]["tone"]
    categories = ", ".join(config["allowed_categories"])
    priorities = ", ".join(config["allowed_priorities"])
    tickets_block = json.dumps(
        [
            {"ticket_id": t["ticket_id"], "text_for_model": t["text_for_model"]}
            for t in normalized
        ],
        indent=2,
    )
    return f"""You are a support ticket triage assistant.

For EACH ticket below, return a JSON object with:
- ticket_id (string, must match input)
- category (one of: {categories})
- priority (one of: {priorities})
- reason (brief string explaining classification)
- suggested_reply (tone: {tone}; at most {max_words} words)
- confidence (float 0.0 to 1.0)

Do NOT include route_to; routing is applied by the pipeline.

Return ONLY a JSON array with one object per ticket, in any order.

Tickets:
{tickets_block}
"""


def _keyword_category(text: str) -> tuple[str, float]:
    """Deterministic fallback classifier from ticket text."""
    lower = text.lower()
    rules: list[tuple[str, list[str], float]] = [
        ("billing_issue", ["charge", "charged", "payment", "deposit", "refund", "invoice"], 0.75),
        ("account_access", ["login", "log in", "password", "credentials", "access", "locked"], 0.78),
        ("bug_report", ["crash", "crashes", "error", "bug", "closes", "broken", "not working"], 0.72),
        ("product_how_to", ["how do", "how to", "export", "download", "where can", "csv"], 0.70),
    ]
    best_cat = "other"
    best_score = 0.45
    for category, keywords, base_conf in rules:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > 0:
            score = min(0.95, base_conf + 0.05 * (hits - 1))
            if score > best_score:
                best_score = score
                best_cat = category
    return best_cat, round(best_score, 2)


def _keyword_priority(text: str, config: dict[str, Any]) -> str:
    lower = text.lower()
    if any(w in lower for w in ("urgent", "urgently", "asap", "immediately", "emergency")):
        return "urgent"
    if any(w in lower for w in ("before market", "need access", "cannot", "can't", "blocked")):
        return "high"
    if any(w in lower for w in ("please help", "help reverse", "crashes", "crash")):
        return "high"
    if any(w in lower for w in ("how do", "export", "download")):
        return "normal"
    return "low" if "low" in config["allowed_priorities"] else "normal"


def _fallback_reply(category: str, ticket_id: str, max_words: int) -> str:
    templates = {
        "billing_issue": (
            f"Thank you for contacting us regarding ticket {ticket_id}. "
            "We are reviewing the billing details and will follow up shortly."
        ),
        "account_access": (
            f"We received your access request for ticket {ticket_id}. "
            "Our team is checking your account and will help restore access."
        ),
        "product_how_to": (
            f"Thanks for your question on ticket {ticket_id}. "
            "We will share step-by-step instructions to complete your request."
        ),
        "bug_report": (
            f"Sorry for the trouble reported in ticket {ticket_id}. "
            "Engineering has been notified and we will update you on next steps."
        ),
        "other": (
            f"Thank you for ticket {ticket_id}. "
            "A specialist will review your message and respond soon."
        ),
    }
    return _truncate_reply(templates.get(category, templates["other"]), max_words)


def _gemini_sdk_available() -> bool:
    try:
        from google import genai  # noqa: F401
        return True
    except ImportError:
        return False


def _call_gemini(prompt: str, model: str, api_key: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            system_instruction=(
                "Respond with valid JSON only: a JSON array of ticket objects. "
                "No markdown fences or extra text."
            ),
        ),
    )
    return response.text or "[]"


def _parse_llm_array(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("LLM response is not a JSON array")
    return data


def _log_recovery(message: str) -> None:
    with RECOVERY_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


def _resolve_llm_backend() -> tuple[str | None, str]:
    """Return (provider_name, model) or (None, heuristic) for fallback."""
    provider = os.environ.get("TRIAGE_LLM_PROVIDER", "").strip().lower()
    if provider == "fallback":
        return None, "keyword-heuristic-v1"

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    use_gemini = provider == "gemini" or (not provider and api_key)
    if use_gemini and api_key and _gemini_sdk_available():
        model = os.environ.get("TRIAGE_LLM_MODEL", "gemini-2.0-flash")
        return "gemini", model
    if use_gemini and api_key and not _gemini_sdk_available():
        _log_recovery(
            "GEMINI_API_KEY set but google-genai not installed; using fallback"
        )
    return None, "keyword-heuristic-v1"


def _coerce_prediction(
    raw: dict[str, Any] | None,
    ticket: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    max_words = config["reply_style"]["max_words"]
    ticket_id = ticket["ticket_id"]
    text = ticket["text_for_model"]

    if raw is None:
        _log_recovery(f"{ticket_id}: missing from LLM response; using fallback")
        category, confidence = _keyword_category(text)
        category = validate_category(config, category)
        priority = validate_priority(config, _keyword_priority(text, config))
        reason = "Recovered: ticket missing from batch LLM response"
        reply = _fallback_reply(category, ticket_id, max_words)
        return _finalize(ticket_id, category, priority, reason, reply, confidence, config)

    try:
        category = validate_category(config, str(raw.get("category", "other")))
        priority = validate_priority(config, str(raw.get("priority", "normal")))
        reason = str(raw.get("reason", "")).strip() or "Classified by triage model"
        reply = _truncate_reply(
            str(raw.get("suggested_reply", _fallback_reply(category, ticket_id, max_words))),
            max_words,
        )
        confidence = float(raw.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))
    except (ValueError, TypeError) as exc:
        _log_recovery(f"{ticket_id}: malformed prediction ({exc}); using fallback")
        category, confidence = _keyword_category(text)
        category = validate_category(config, category)
        priority = validate_priority(config, _keyword_priority(text, config))
        reason = f"Recovered: malformed LLM fields ({exc})"
        reply = _fallback_reply(category, ticket_id, max_words)

    return _finalize(ticket_id, category, priority, reason, reply, confidence, config)


def _finalize(
    ticket_id: str,
    category: str,
    priority: str,
    reason: str,
    reply: str,
    confidence: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "category": category,
        "priority": priority,
        "reason": reason,
        "suggested_reply": reply,
        "route_to": route_for_category(config, category),
        "confidence": round(confidence, 2),
    }


def predict_triage(
    normalized: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    output_path: Path = PREDICTIONS_PATH,
    llm_log_path: Path = LLM_LOG_PATH,
) -> list[dict[str, Any]]:
    prompt = _build_prompt(normalized, config)
    provider_name, model = _resolve_llm_backend()

    raw_by_id: dict[str, dict[str, Any]] = {}
    log_llm_call(
        llm_log_path,
        stage="TRIAGE_PREDICTED",
        provider=provider_name or "deterministic_fallback",
        model=model,
        prompt=prompt,
        input_artifacts=[str(NORMALIZED_PATH), str(CONFIG_PATH)],
        output_artifact=str(output_path),
    )

    if provider_name == "gemini":
        api_key = os.environ["GEMINI_API_KEY"].strip()
        try:
            content = _call_gemini(prompt, model, api_key)
            parsed = _parse_llm_array(content)
            for item in parsed:
                if isinstance(item, dict) and "ticket_id" in item:
                    raw_by_id[str(item["ticket_id"])] = item
        except (json.JSONDecodeError, ValueError) as exc:
            _log_recovery(f"Batch Gemini parse failed ({exc}); per-ticket fallback")
        except Exception as exc:
            _log_recovery(f"Gemini API call failed ({exc}); per-ticket fallback")
    else:
        for ticket in normalized:
            cat, conf = _keyword_category(ticket["text_for_model"])
            pri = _keyword_priority(ticket["text_for_model"], config)
            raw_by_id[ticket["ticket_id"]] = {
                "ticket_id": ticket["ticket_id"],
                "category": cat,
                "priority": pri,
                "reason": "Classified by deterministic keyword heuristic (no Gemini)",
                "suggested_reply": _fallback_reply(
                    validate_category(config, cat),
                    ticket["ticket_id"],
                    config["reply_style"]["max_words"],
                ),
                "confidence": conf,
            }

    predictions: list[dict[str, Any]] = []
    for ticket in normalized:
        pred = _coerce_prediction(
            raw_by_id.get(ticket["ticket_id"]), ticket, config
        )
        if word_count(pred["suggested_reply"]) > config["reply_style"]["max_words"]:
            pred["suggested_reply"] = _truncate_reply(
                pred["suggested_reply"], config["reply_style"]["max_words"]
            )
        predictions.append(pred)

    # Stable output order by ticket_id
    predictions.sort(key=lambda p: p["ticket_id"])
    output_path.write_text(
        json.dumps(predictions, indent=2) + "\n", encoding="utf-8"
    )
    return predictions
