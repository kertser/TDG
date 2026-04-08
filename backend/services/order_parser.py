"""
OrderParser – classifies radio messages and extracts structured data.

Three-tier model routing for cost optimization:
  1. Keyword fallback runs FIRST — if confidence ≥ 0.8, skip LLM entirely
  2. If confidence ≥ 0.5 (partial match), use cheap nano model (gpt-4o-mini)
  3. If confidence < 0.5 (ambiguous), use full model (gpt-4.1)

Also supports:
  - Local model fallback (Qwen/llama.cpp) via LOCAL_MODEL_URL when no API key
  - Bilingual EN/RU military radio communications
  - All output validated through Pydantic
"""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from backend.config import settings
from backend.schemas.order import ParsedOrderData, MessageClassification, DetectedLanguage
from backend.prompts.order_parser import (
    SYSTEM_PROMPT,
    build_user_message,
    build_unit_roster,
    build_grid_info,
)

logger = logging.getLogger(__name__)

# Max retries on LLM parse failure
MAX_RETRIES = 1

# Confidence thresholds for model routing
CONF_SKIP_LLM = 0.80    # keyword result good enough, skip LLM entirely
CONF_USE_NANO = 0.50     # partial match, use cheap model
# Below CONF_USE_NANO → use full model


class OrderParser:
    """
    Classifies radio messages and extracts structured order data.

    Uses a three-tier approach:
    1. Fast keyword parsing (always runs first, ~0ms)
    2. Cheap LLM (gpt-4o-mini / nano) for medium-confidence cases
    3. Full LLM (gpt-4.1) for ambiguous/complex messages

    All output is validated through Pydantic before returning.
    """

    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._local_client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    def _get_local_client(self) -> AsyncOpenAI | None:
        """Get client for local model (llama.cpp / vLLM / Ollama)."""
        if not settings.LOCAL_MODEL_URL:
            return None
        if self._local_client is None:
            self._local_client = AsyncOpenAI(
                api_key="local",
                base_url=settings.LOCAL_MODEL_URL,
            )
        return self._local_client

    async def parse(
        self,
        original_text: str,
        units: list[dict],
        grid_info: dict | None = None,
        game_time: str = "",
        issuer_side: str | None = None,
    ) -> ParsedOrderData:
        """
        Parse a radio message into structured data.

        Three-tier routing:
        1. Run keyword parser first (instant)
        2. If keyword confidence ≥ 0.8 → return directly (skip LLM, save money)
        3. If keyword confidence ≥ 0.5 → use cheap nano model (gpt-4o-mini)
        4. If keyword confidence < 0.5 → use full model (gpt-4.1)
        5. If no API key → try local model → fall back to keyword result
        """
        # ── Step 1: Always run keyword parser first ──
        keyword_result = self._fallback_parse(original_text)

        # ── Step 2: High confidence → skip LLM ──
        if keyword_result.confidence >= CONF_SKIP_LLM:
            logger.info(
                "OrderParser: keyword confidence %.2f ≥ %.2f, skipping LLM. class=%s",
                keyword_result.confidence, CONF_SKIP_LLM,
                keyword_result.classification.value,
            )
            return keyword_result

        # ── Step 3: Determine which model to use ──
        if not settings.OPENAI_API_KEY:
            # No cloud API → try local model
            local_client = self._get_local_client()
            if local_client:
                logger.info("OrderParser: no API key, using local model at %s",
                            settings.LOCAL_MODEL_URL)
                result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=local_client,
                    model=settings.LOCAL_MODEL_NAME,
                    issuer_side=issuer_side,
                )
                return result if result else keyword_result
            else:
                logger.warning("OrderParser: no API key and no local model — using keyword result")
                return keyword_result

        # Choose model tier based on keyword confidence
        if keyword_result.confidence >= CONF_USE_NANO:
            model = settings.OPENAI_MODEL_NANO
            tier = "nano"
        else:
            model = settings.OPENAI_MODEL
            tier = "full"

        logger.info(
            "OrderParser: keyword confidence %.2f → using %s model (%s)",
            keyword_result.confidence, tier, model,
        )

        # ── Step 4: Call LLM ──
        result = await self._call_llm(
            original_text, units, grid_info, game_time,
            client=self._get_client(),
            model=model,
            issuer_side=issuer_side,
        )
        return result if result else keyword_result

    async def _call_llm(
        self,
        original_text: str,
        units: list[dict],
        grid_info: dict | None,
        game_time: str,
        client: AsyncOpenAI,
        model: str,
        issuer_side: str | None = None,
    ) -> ParsedOrderData | None:
        """Call LLM and return parsed result, or None on failure."""
        # Filter units to issuer's side only (reduce prompt tokens)
        if issuer_side:
            filtered_units = [
                u for u in units
                if u.get("side") == issuer_side and not u.get("is_destroyed")
            ]
        else:
            filtered_units = [u for u in units if not u.get("is_destroyed")]

        system = SYSTEM_PROMPT.format(
            unit_roster=build_unit_roster(filtered_units),
            grid_info=build_grid_info(grid_info),
            game_time=game_time or "Unknown",
        )
        user_msg = build_user_message(original_text)

        last_error = None
        raw_content = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=1000,
                )

                raw_content = response.choices[0].message.content
                if not raw_content:
                    raise ValueError("Empty LLM response")

                raw_json = json.loads(raw_content)
                parsed = ParsedOrderData.model_validate(raw_json)

                logger.info(
                    "OrderParser[%s]: classified=%s lang=%s conf=%.2f (attempt %d)",
                    model, parsed.classification.value,
                    parsed.language.value, parsed.confidence, attempt + 1,
                )
                return parsed

            except json.JSONDecodeError as e:
                last_error = f"JSON decode error: {e}"
                logger.warning("OrderParser[%s] attempt %d: %s. Raw: %s",
                               model, attempt + 1, last_error,
                               raw_content[:200] if raw_content else "empty")
            except Exception as e:
                last_error = str(e)
                logger.warning("OrderParser[%s] attempt %d: %s", model, attempt + 1, last_error)

        logger.error("OrderParser[%s]: failed after %d attempts (%s)",
                      model, MAX_RETRIES + 1, last_error)
        return None

    def _fallback_parse(self, text: str) -> ParsedOrderData:
        """
        Simple keyword-based fallback when LLM is unavailable.
        """
        text_lower = text.lower()

        # Language detection (simple heuristic)
        has_cyrillic = any('\u0400' <= c <= '\u04ff' for c in text)
        lang = DetectedLanguage.ru if has_cyrillic else DetectedLanguage.en

        # Classification keywords
        command_kw_en = ["move", "advance", "attack", "defend", "hold", "observe", "withdraw",
                         "retreat", "support", "halt", "stop", "flank", "engage"]
        command_kw_ru = ["выдвигай", "двигай", "атак", "оборон", "удержи", "наблюда", "отход",
                         "отступ", "поддерж", "стой", "стоп", "обход", "огонь"]
        status_req_kw = ["доложи", "report", "обстанов", "что у вас", "what's happening", "status"]
        ack_kw = ["так точно", "roger", "wilco", "понял", "copy", "выполня", "принял"]
        report_kw = ["здесь", "this is", "наблюдаем", "обнаружен", "потери", "контакт",
                     "находимся", "spotted", "taking fire", "casualties"]

        classification = MessageClassification.unclear
        order_type = None

        # Strong sender-identification signals → status report or ack
        is_self_report = any(kw in text_lower for kw in ["здесь ", "this is "])

        if any(kw in text_lower for kw in ack_kw):
            classification = MessageClassification.acknowledgment
        elif any(kw in text_lower for kw in status_req_kw):
            classification = MessageClassification.status_request
            order_type = "report_status"
        elif is_self_report and any(kw in text_lower for kw in report_kw):
            # "Здесь [unit]. ..." is almost always a status report
            classification = MessageClassification.status_report
        elif any(kw in text_lower for kw in report_kw) and not any(kw in text_lower for kw in command_kw_en + command_kw_ru):
            classification = MessageClassification.status_report
        elif any(kw in text_lower for kw in command_kw_en + command_kw_ru):
            classification = MessageClassification.command
            # Determine order type
            if any(kw in text_lower for kw in ["move", "advance", "выдвигай", "двигай", "обход"]):
                order_type = "move"
            elif any(kw in text_lower for kw in ["attack", "engage", "атак", "огонь"]):
                order_type = "attack"
            elif any(kw in text_lower for kw in ["defend", "hold", "оборон", "удержи"]):
                order_type = "defend"
            elif any(kw in text_lower for kw in ["observe", "наблюда"]):
                order_type = "observe"
            elif any(kw in text_lower for kw in ["withdraw", "retreat", "отход", "отступ"]):
                order_type = "withdraw"
            elif any(kw in text_lower for kw in ["halt", "stop", "стой", "стоп"]):
                order_type = "halt"

        # Extract snail/grid references with regex
        import re
        location_refs = []
        # Match patterns like B8-2-4, C7-8-3, A1-1
        snail_pattern = re.compile(r'[A-Za-z]\d+(?:-\d){1,3}')
        for m in snail_pattern.finditer(text):
            location_refs.append({
                "source_text": m.group(),
                "ref_type": "snail",
                "normalized": m.group().upper(),
            })
        # Match grid squares like B8, C7
        grid_pattern = re.compile(r'\b([A-Za-z])(\d{1,2})\b')
        for m in grid_pattern.finditer(text):
            full = m.group().upper()
            # Don't duplicate if already caught as snail
            if not any(lr["normalized"].startswith(full) for lr in location_refs):
                location_refs.append({
                    "source_text": m.group(),
                    "ref_type": "grid",
                    "normalized": full,
                })

        # Match coordinate patterns: "48.8566,2.3522" or "48.8566, 2.3522"
        # Also "координаты 48.8566, 2.3522", "coords 48.8566 2.3522"
        coord_pattern = re.compile(
            r'(?:координат\w*|coords?|точк\w*|point)?\s*'
            r'(-?\d{1,3}\.\d{2,8})\s*[,;\s]\s*(-?\d{1,3}\.\d{2,8})',
            re.IGNORECASE,
        )
        for m in coord_pattern.finditer(text):
            coord_str = f"{m.group(1)},{m.group(2)}"
            # Don't duplicate
            if not any(lr["normalized"] == coord_str for lr in location_refs):
                location_refs.append({
                    "source_text": m.group().strip(),
                    "ref_type": "coordinate",
                    "normalized": coord_str,
                })

        # Extract target unit references from text
        # Look for common patterns: "1st Platoon", "Первый взвод", "Recon Team", etc.
        target_unit_refs = []
        # English ordinal + unit patterns
        en_unit_pat = re.compile(
            r'\b(\d+(?:st|nd|rd|th)\s+(?:platoon|company|section|team|squad|battery|group)'
            r'(?:,?\s*[A-Z]\s+(?:company|battalion|brigade))?)\b',
            re.IGNORECASE,
        )
        for m in en_unit_pat.finditer(text):
            target_unit_refs.append(m.group().strip().rstrip(","))

        # Russian unit patterns: "первый/второй/... взвод/рота/..."
        ru_unit_pat = re.compile(
            r'(?:перв\w+|втор\w+|трет\w+|четвёрт\w+|четверт\w+|пят\w+|шест\w+'
            r'|седьм\w+|восьм\w+|девят\w+|десят\w+)\s+'
            r'(?:взвод\w*|рот\w*|отделени\w*|групп\w*|команд\w*|батаре\w*|секци\w*)',
            re.IGNORECASE,
        )
        for m in ru_unit_pat.finditer(text):
            target_unit_refs.append(m.group().strip())

        # Named units: "Recon Team", "Mortar Section", "Tank Platoon"
        named_pat = re.compile(
            r'\b((?:recon|mortar|tank|sniper|engineer|artillery|logistics|observation|combat'
            r'|infantry|mechanized)\s+(?:team|section|platoon|company|squad|battery|group)'
            r'(?:\s+\d+)?)\b',
            re.IGNORECASE,
        )
        for m in named_pat.finditer(text):
            ref = m.group().strip()
            if ref not in target_unit_refs:
                target_unit_refs.append(ref)

        # Russian named: "разведгруппа", "миномётная секция"
        ru_named_pat = re.compile(
            r'\b((?:развед\w*|миномёт\w*|минометн\w*|танк\w*|снайпер\w*|сапёрн\w*'
            r'|артиллер\w*|инженерн\w*)\s*(?:группа|секция|взвод|рота|команда)?)\b',
            re.IGNORECASE,
        )
        for m in ru_named_pat.finditer(text):
            ref = m.group().strip()
            if ref and ref not in target_unit_refs and len(ref) > 3:
                target_unit_refs.append(ref)

        # Sender ref (for ack/report messages)
        sender_ref = None
        sender_match = re.search(r'(?:здесь|this is)\s+(.+?)(?:[,\.!]|приём|$)', text_lower)
        if sender_match:
            sender_ref = sender_match.group(1).strip()

        from backend.schemas.order import LocationRefRaw
        # Speed detection
        speed = None
        if any(kw in text_lower for kw in ["slow", "careful", "cautious", "медленн", "осторожн", "скрытно"]):
            speed = "slow"
        elif any(kw in text_lower for kw in ["fast", "rapid", "quick", "urgent", "срочно", "быстр", "немедленно"]):
            speed = "fast"

        # Engagement rules
        engagement_rules = None
        if any(kw in text_lower for kw in ["hold fire", "не стрелять", "огонь не открывать"]):
            engagement_rules = "hold_fire"
        elif any(kw in text_lower for kw in ["fire at will", "огонь по готовности"]):
            engagement_rules = "fire_at_will"
        elif any(kw in text_lower for kw in ["return fire only", "ответный огонь"]):
            engagement_rules = "return_fire_only"

        return ParsedOrderData(
            classification=classification,
            language=lang,
            target_unit_refs=target_unit_refs,
            sender_ref=sender_ref,
            order_type=order_type,
            location_refs=[LocationRefRaw(**lr) for lr in location_refs],
            speed=speed,
            engagement_rules=engagement_rules,
            confidence=self._compute_keyword_confidence(
                classification, order_type, location_refs,
                target_unit_refs, speed, engagement_rules,
            ),
            ambiguities=["Parsed by keyword fallback — no LLM"],
        )

    @staticmethod
    def _compute_keyword_confidence(
        classification: MessageClassification,
        order_type: str | None,
        location_refs: list[dict],
        target_unit_refs: list[str],
        speed: str | None,
        engagement_rules: str | None,
    ) -> float:
        """
        Compute confidence score for keyword-parsed result.

        Higher confidence when more elements are successfully extracted.
        This drives the 3-tier model routing decision.
        """
        if classification == MessageClassification.unclear:
            return 0.15

        conf = 0.45  # base: classification was determined

        # Acknowledgments and status reports are easy to identify
        if classification == MessageClassification.acknowledgment:
            return 0.90  # very clear pattern
        if classification == MessageClassification.status_report:
            return 0.85

        # Status requests are also clear
        if classification == MessageClassification.status_request:
            conf = 0.80
            if target_unit_refs:
                conf += 0.10
            return min(conf, 0.95)

        # For commands: confidence depends on how much was extracted
        if classification == MessageClassification.command:
            if order_type:
                conf += 0.15  # order type identified
            if location_refs:
                # Snail/coordinate refs are high-quality matches
                has_precise = any(
                    lr["ref_type"] in ("snail", "coordinate")
                    for lr in location_refs
                )
                conf += 0.15 if has_precise else 0.08
            if target_unit_refs:
                conf += 0.10  # unit identified
            if speed:
                conf += 0.03
            if engagement_rules:
                conf += 0.03

        return min(conf, 0.95)


# Singleton
order_parser = OrderParser()



