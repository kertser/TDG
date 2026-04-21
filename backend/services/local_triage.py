"""
LocalTriageClassifier — lightweight local LLM for trivial classification tasks.

Handles ONLY:
  1. Message type classification (command / ack / report / request / unclear)
  2. Language detection (en / ru)
  3. Complexity hint (simple / compound) — compound commands force full cloud model

Prompt is ~200 tokens. No doctrine, no context, no few-shot examples.
If the local LLM is unavailable or times out, returns None (caller falls back
to keyword heuristics).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from backend.config import settings
from backend.schemas.order import MessageClassification, DetectedLanguage
from backend.services.llm_client import get_local_client

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM_PROMPT = """You classify military radio messages. Output JSON only.

Classify the message into exactly one category:
- "command" — an actionable order (move, attack, defend, fire, observe, halt, retreat, etc.)
- "complex_command" — a COMPOUND order with multiple sequential actions (e.g. "move to X then attack Y", "halt, set up defense, request fire support")
- "acknowledgment" — confirming receipt ("roger", "wilco", "понял", "так точно", "выполняю")
- "status_report" — unit reporting its own situation ("здесь...", "this is...", "находимся", "taking fire")
- "status_request" — asking for information ("доложите", "report status", "что у вас", "where are you")
- "unclear" — cannot determine

Detect language: "en" or "ru".

Signs of complex_command (vs simple command):
- Multiple action verbs: "move THEN attack", "halt AND defend"
- Sequential steps: "first..., then...", "сначала..., затем..."
- Conditional clauses: "once you reach..., hold", "когда выполните..., займите"
- Coordination between multiple units in one message

Output format: {"classification": "...", "language": "..."}"""

# ── Keyword-based compound detection (fast, no LLM needed) ──
_COMPOUND_MARKERS_EN = [
    "and then", "then ", "after that", "once done", "when complete",
    "followed by", "next ", "subsequently", "first ", "second ",
    "once you reach", "once at", "when you arrive", "upon arrival",
    "after reaching", "before moving", "while holding", "before attacking",
    "continue to", "proceed to then", "set up and then",
]
_COMPOUND_MARKERS_RU = [
    "затем", "потом", "после этого", "после чего", "а потом",
    "далее", "следом", "и потом", "когда выполните", "по выполнении",
    "сначала", "во-первых", "во-вторых", "когда доберётесь",
    "когда доберетесь", "по прибытии", "по достижении",
    "после выхода", "перед атакой", "перед выходом", "а затем",
    "после занятия", "по занятии",
]


def detect_compound_keyword(text: str) -> bool:
    """Fast keyword check for compound/multi-step commands."""
    text_lower = text.lower()
    if any(m in text_lower for m in _COMPOUND_MARKERS_EN + _COMPOUND_MARKERS_RU):
        return True
    # Multiple sentences with action verbs in different sentences
    sentences = re.split(r'[.!;]\s+', text.strip())
    if len(sentences) >= 2:
        action_verbs_en = {"move", "attack", "defend", "halt", "fire", "observe", "withdraw",
                           "advance", "engage", "support", "breach", "hold", "retreat",
                           "resupply", "disengage", "split", "merge", "deploy",
                           "screen", "recon", "eliminate", "destroy", "suppress",
                           "rally", "regroup", "consolidate", "overwatch", "cover",
                           "escort", "patrol", "ambush", "cross", "ford",
                           "construct", "build", "entrench", "fortify",
                           "request fire", "call for fire", "cease fire"}
        action_verbs_ru = {"выдвигай", "атак", "оборон", "стой", "огонь", "наблюда", "отход",
                           "марш", "удержи", "поддерж", "прикрой", "отступ", "разведай",
                           "окопай", "разминируй", "навести", "поставь",
                           "уничтож", "захвати", "занять", "штурмуй", "перегруппируй",
                           "засад", "патрулируй", "форсируй", "переправ", "минируй",
                           "построй", "укрепи", "эвакуир", "подавить",
                           "прекрати огонь", "запроси огонь", "вызови огонь"}
        action_count = 0
        for sent in sentences:
            sent_lower = sent.lower()
            if any(v in sent_lower for v in action_verbs_en) or \
               any(v in sent_lower for v in action_verbs_ru):
                action_count += 1
        if action_count >= 2:
            return True
    return False


@dataclass(frozen=True)
class TriageResult:
    classification: MessageClassification
    language: DetectedLanguage
    confidence: float
    is_compound: bool = False  # True if the command has multiple sequential steps


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
            # Even without local LLM, detect compound commands via keywords
            if detect_compound_keyword(text):
                return TriageResult(
                    classification=MessageClassification.command,
                    language=DetectedLanguage.ru if any('\u0400' <= c <= '\u04ff' for c in text) else DetectedLanguage.en,
                    confidence=0.40,  # low confidence → forces full cloud model
                    is_compound=True,
                )
            return None

        # Backoff after recent failure
        now = time.monotonic()
        if now - self._last_failure_time < self._backoff_seconds:
            # Still do keyword-based compound check
            if detect_compound_keyword(text):
                return TriageResult(
                    classification=MessageClassification.command,
                    language=DetectedLanguage.ru if any('\u0400' <= c <= '\u04ff' for c in text) else DetectedLanguage.en,
                    confidence=0.40,
                    is_compound=True,
                )
            return None

        client_info = get_local_client()
        if client_info is None:
            if detect_compound_keyword(text):
                return TriageResult(
                    classification=MessageClassification.command,
                    language=DetectedLanguage.ru if any('\u0400' <= c <= '\u04ff' for c in text) else DetectedLanguage.en,
                    confidence=0.40,
                    is_compound=True,
                )
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

            # Map complex_command → command with compound flag
            is_compound_llm = (cls_str == "complex_command")
            if is_compound_llm:
                cls_str = "command"

            # Also check keyword-based compound detection
            is_compound_kw = detect_compound_keyword(text)
            is_compound = is_compound_llm or is_compound_kw

            try:
                classification = MessageClassification(cls_str)
            except ValueError:
                classification = MessageClassification.unclear

            try:
                language = DetectedLanguage(lang_str)
            except ValueError:
                language = DetectedLanguage.en

            # Compound commands get LOW confidence to force full cloud model
            if is_compound and classification == MessageClassification.command:
                confidence = 0.35
            else:
                # Local triage confidence is moderate — it's a hint, not authoritative
                confidence = 0.70 if classification != MessageClassification.unclear else 0.30

            logger.info(
                "LocalTriage: class=%s lang=%s compound=%s (model=%s)",
                classification.value, language.value, is_compound, client_info.model,
            )
            return TriageResult(
                classification=classification,
                language=language,
                confidence=confidence,
                is_compound=is_compound,
            )

        except Exception as e:
            logger.warning("LocalTriage: failed (%s) — will back off %.0fs", e, self._backoff_seconds)
            LocalTriageClassifier._last_failure_time = time.monotonic()
            # Still try keyword-based compound detection
            if detect_compound_keyword(text):
                return TriageResult(
                    classification=MessageClassification.command,
                    language=DetectedLanguage.ru if any('\u0400' <= c <= '\u04ff' for c in text) else DetectedLanguage.en,
                    confidence=0.40,
                    is_compound=True,
                )
            return None


# Singleton
local_triage = LocalTriageClassifier()

