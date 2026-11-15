"""Gemini-powered AI chatbot for Pakistani fashion brand analytics.

Falls back to rule-based engine if GEMINI_API_KEY is not set.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent

SYSTEM_PROMPT = textwrap.dedent("""
You are an expert Pakistani social media marketing analyst specialising in fashion brands.
You help brands understand what content drives engagement with Pakistani audiences.

RULES:
- Answer ONLY from the data context provided. Never invent numbers.
- Always cite how many posts (n=X) your answer is based on.
- Give specific, actionable recommendations (day, time, format).
- If data is insufficient (n < 8), say so honestly.
- Understand Pakistani cultural context: Eid, lawn season, bridal season, Pakistani fashion.
- Keep answers concise — under 180 words.
- End every answer with one follow-up question the user might want to ask.
- Format: plain text, no markdown headers, no bullet symbols — the UI handles formatting.

BRANDS IN SCOPE: Khaadi, Sapphire, Gul Ahmed, Maria B, Sana Safinaz
PLATFORMS: Instagram, Facebook, TikTok, YouTube
""").strip()

FALLBACK_SUGGESTIONS = [
    "Which brand gets the most views on YouTube?",
    "Which content format performs best?",
    "What is the best day to post for Pakistani audiences?",
    "Compare Khaadi and Sapphire performance",
    "Which platform works best for fashion brands?",
    "What type of post gets the most engagement?",
]


def _build_context(engine_answer: dict, total_posts: int, platform: str | None, brand: str | None) -> str:
    """Format engine answer as text context for Gemini."""
    lines = [f"Dataset: {total_posts} posts collected"]
    if platform:
        lines.append(f"Filtered to platform: {platform}")
    if brand:
        lines.append(f"Filtered to brand: {brand}")
    lines.append("")

    text = engine_answer.get("text", "")
    if text:
        lines.append(f"Analytics result: {text}")

    evidence = engine_answer.get("evidence", "")
    if evidence:
        lines.append(f"Evidence: {evidence}")

    for block in engine_answer.get("blocks", []):
        if block.get("type") == "bars":
            lines.append(f"\n{block.get('title', 'Data')}:")
            for item in block.get("items", [])[:8]:
                flag = " (small sample)" if item.get("flag") else ""
                lines.append(f"  {item['label']}: {item['display']} (n={item.get('n', '?')}){flag}")

    for note in engine_answer.get("notes", []):
        if note.get("level") != "method":
            lines.append(f"Note: {note.get('text', '')}")

    return "\n".join(lines)


def ask_gemini(
    question: str,
    history: list[dict],
    engine_answer: dict,
    total_posts: int,
    platform: str | None = None,
    brand: str | None = None,
) -> dict:
    """
    Ask Gemini about brand analytics. Returns:
      {answer, suggestions, used_ai, evidence, n_posts}
    Falls back to rule-based if no API key.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        # Graceful fallback: return rule-based answer with suggestions
        return {
            "answer": engine_answer.get("text", "No answer available."),
            "evidence": engine_answer.get("evidence", ""),
            "suggestions": FALLBACK_SUGGESTIONS[:4],
            "used_ai": False,
            "note": "Add GEMINI_API_KEY to .env to enable AI-powered answers.",
        }

    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT,
        )

        # Build conversation history (last 4 turns max)
        chat_history = []
        for turn in history[-4:]:
            role = "user" if turn.get("role") == "user" else "model"
            chat_history.append({"role": role, "parts": [turn.get("content", "")]})

        # Build the prompt with data context
        context = _build_context(engine_answer, total_posts, platform, brand)
        prompt = f"{context}\n\nUser question: {question}"

        chat = model.start_chat(history=chat_history)
        response = chat.send_message(prompt)
        answer_text = response.text.strip()

        # Generate follow-up suggestions
        suggestion_prompt = (
            f"The user just asked: '{question}'. "
            "Suggest 3 short follow-up questions they might ask about Pakistani fashion brand analytics. "
            "Return only a JSON array of 3 strings, nothing else."
        )
        try:
            sug_response = model.generate_content(suggestion_prompt)
            import json, re
            raw = sug_response.text.strip()
            # Extract JSON array from response
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            suggestions = json.loads(match.group(0)) if match else FALLBACK_SUGGESTIONS[:3]
        except Exception:
            suggestions = FALLBACK_SUGGESTIONS[:3]

        return {
            "answer": answer_text,
            "evidence": engine_answer.get("evidence", ""),
            "suggestions": suggestions[:4],
            "used_ai": True,
        }

    except Exception as exc:
        # Any Gemini error → fall back to rule-based
        return {
            "answer": engine_answer.get("text", "Could not get an answer."),
            "evidence": engine_answer.get("evidence", ""),
            "suggestions": FALLBACK_SUGGESTIONS[:3],
            "used_ai": False,
            "note": f"AI unavailable ({type(exc).__name__}): rule-based answer shown.",
        }

