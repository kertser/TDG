"""
LocalTriageClassifier — lightweight local LLM for trivial classification tasks.

Handles ONLY:
  1. Message type classification (command / ack / report / request / unclear)
  2. Language detection (en / ru)

Prompt is ~200 tokens. No doctrine, no context, no few-shot examples.
If the local LLM is unavailable or times out, returns None (caller falls back
to keyword heuristics).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from backend.config import settings
from backend.schemas.order import MessageClassification, DetectedLanguage
from backend.services.llm_client import get_local_client

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM_PROMPT = """You classify military radio messages. Output JSON only.

Classify the message into exactly one category:
- "command" — an actionable order (move, attack, defend, fire, observe, halt, retreat, etc.)
- "acknowledgment" — confirming receipt ("roger", "wilco", "понял", "так точно", "выполняю")
- "status_report" — unit reporting its own situation ("здесь...", "this is...", "находимся", "taking fire")
- "status_request" — asking for information ("доложите", "report status", "что у вас", "where are you")
- "unclear" — cannot determine

Detect language: "en" or "ru".

Output format: {"classification": "...", "language": "..."}"""


@dataclass(frozen=True)
class TriageResult:
    classification: MessageClassification
    language: DetectedLanguage
    confidence: float


class LocalTriageClassifier:
    """
    Uses a small local LLM (1-3B params) for cheap message classification.
    Falls back gracefully to None if unavailable.
    """

    _last_failure_time: float = 0.0
    _backoff_seconds: float = 30.0  # skip local LLM for 30s after a failure

    async def classify(self, text: str) -> TriageResult | None:
        """
        Classify a message using local LLM.
        Returns None if local LLM is unavailable, disabled, or times out.
        """
        if not settings.LOCAL_TRIAGE_ENABLED or not settings.LOCAL_MODEL_URL:
            return None

        # Backoff after recent failure
        now = time.monotonic()
        if now - self._last_failure_time < self._backoff_seconds:
            return None

        client_info = get_local_client()
        if client_info is None:
            return None

        try:
            response = await client_info.client.chat.completions.create(
                model=client_info.model,
                messages=[
                    {"role": "system", "content": _TRIAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_completion_tokens=64,
            )

            raw = response.choices[0].message.content
            if not raw:
                return None

            # Strip markdown fences
            content = raw.strip()
            if content.startswith("```"):
                first_nl = content.find("\n")
                if first_nl != -1:
                    content = content[first_nl + 1:]
                if content.endswith("```"):
                    content = content[:-3].strip()

            data = json.loads(content)

            cls_str = data.get("classification", "unclear")
            lang_str = data.get("language", "en")

            try:
                classification = MessageClassification(cls_str)
            except ValueError:
                classification = MessageClassification.unclear

            try:
                language = DetectedLanguage(lang_str)
            except ValueError:
                language = DetectedLanguage.en

            # Local triage confidence is moderate — it's a hint, not authoritative
            confidence = 0.70 if classification != MessageClassification.unclear else 0.30

            logger.info(
                "LocalTriage: class=%s lang=%s (model=%s)",
                classification.value, language.value, client_info.model,
            )
            return TriageResult(
                classification=classification,
                language=language,
                confidence=confidence,
            )

        except Exception as e:
            logger.warning("LocalTriage: failed (%s) — will back off %.0fs", e, self._backoff_seconds)
            LocalTriageClassifier._last_failure_time = time.monotonic()
            return None


# Singleton
local_triage = LocalTriageClassifier()

