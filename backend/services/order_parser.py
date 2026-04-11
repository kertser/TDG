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

def _build_height_tops_context(grid_info: dict | None) -> str:
    """Build a text description of available height tops for the LLM prompt."""
    if not grid_info or "height_tops" not in grid_info:
        return ""
    peaks = grid_info["height_tops"]
    if not peaks:
        return ""
    lines = ["Available named height tops on the map:"]
    for p in peaks[:20]:
        lines.append(f"  - {p['label']} ({p['label_ru']}) at grid {p.get('snail_path', '?')}")
    return "\n".join(lines)

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
        force_full_model: bool = False,
    ) -> ParsedOrderData:
        """
        Parse a radio message into structured data.

        Three-tier routing:
        1. Run keyword parser first (instant)
        2. If keyword confidence ≥ 0.8 → return directly (skip LLM, save money)
        3. If keyword confidence ≥ 0.5 → use cheap nano model (gpt-4o-mini)
        4. If keyword confidence < 0.5 → use full model (gpt-4.1)
        5. If no API key → try local model → fall back to keyword result

        If force_full_model=True, skip tiers 1-3 and always use the full model.
        """
        # ── Step 1: Always run keyword parser first ──
        keyword_result = self._fallback_parse(original_text)

        # ── Step 2: If forced full model, skip keyword routing ──
        if force_full_model:
            if settings.OPENAI_API_KEY:
                logger.info("OrderParser: force_full_model — using %s", settings.OPENAI_MODEL)
                result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=self._get_client(),
                    model=settings.OPENAI_MODEL,
                    issuer_side=issuer_side,
                )
                return result if result else keyword_result
            return keyword_result

        # ── Step 3: High confidence → skip LLM ──
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

        # ── Step 5: If result is "unclear" and we used a cheaper model, escalate to full model ──
        if result and result.classification == MessageClassification.unclear and tier != "full":
            logger.info(
                "OrderParser: %s model classified as 'unclear' — escalating to full model (%s)",
                tier, settings.OPENAI_MODEL,
            )
            full_result = await self._call_llm(
                original_text, units, grid_info, game_time,
                client=self._get_client(),
                model=settings.OPENAI_MODEL,
                issuer_side=issuer_side,
            )
            if full_result and full_result.classification != MessageClassification.unclear:
                return full_result
            # Full model also unclear — return the full model result (or original)
            if full_result:
                return full_result

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
            height_tops_context=_build_height_tops_context(grid_info),
        )
        user_msg = build_user_message(original_text)

        last_error = None
        raw_content = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                create_kwargs = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_completion_tokens=1000,
                )
                try:
                    response = await client.chat.completions.create(**create_kwargs)
                except Exception as api_err:
                    # Some models don't support max_completion_tokens — retry without it
                    err_str = str(api_err)
                    if "max_tokens" in err_str or "max_completion_tokens" in err_str:
                        create_kwargs.pop("max_completion_tokens", None)
                        response = await client.chat.completions.create(**create_kwargs)
                    elif "temperature" in err_str:
                        create_kwargs.pop("temperature", None)
                        response = await client.chat.completions.create(**create_kwargs)
                        create_kwargs.pop("max_completion_tokens", None)
                        response = await client.chat.completions.create(**create_kwargs)
                    else:
                        raise

                raw_content = response.choices[0].message.content
                if not raw_content:
                    raise ValueError("Empty LLM response")

                raw_json = json.loads(raw_content)
                parsed = ParsedOrderData.model_validate(raw_json)

                # Normalize formation values (LLM may return non-canonical names)
                if parsed.formation:
                    FORMATION_NORMALIZE = {
                        "staggered_column": "staggered",
                        "echelon": "echelon_right",
                    }
                    parsed.formation = FORMATION_NORMALIZE.get(parsed.formation, parsed.formation)

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
                         "retreat", "support", "halt", "stop", "flank", "engage", "fire at",
                         "fire on", "fire mission", "shoot", "disengage", "break contact",
                         "hit any", "hit enemy", "engage any", "open fire", "suppress",
                         "capture", "seize", "take", "occupy",
                         "eliminate", "destroy", "neutralize",
                         "call for fire", "request fire", "direct artillery", "call artillery",
                         "request artillery"]
        command_kw_ru = ["выдвигай", "двигай", "движен", "марш", "атак", "оборон", "удержи", "наблюда", "отход",
                         "отступ", "поддерж", "стой", "стоп", "обход", "огонь по", "огонь на",
                         "открыть огонь", "стреляй", "разорвать контакт", "разорви контакт",
                         "выйти из боя", "выйди из боя", "отцепи", "перестрои", "построение",
                         "поразить", "бей по", "удар по", "подавить", "подавляющ",
                         "захвати", "захват", "овладе", "занять", "займ",
                         "продолжай", "будьте готовы", "координируй", "организуй",
                         "откройте огонь", "открывай", "выдвину", "приказываю",
                         "наведите", "наведи", "наводите", "наводи",
                         "вызовите огонь", "вызови огонь", "запросите огонь", "запроси огонь",
                         "артиллерию на", "миномёт на", "минометн на"]
        status_req_kw = ["доложи", "report", "обстанов", "что у вас", "what's happening", "status"]
        ack_kw = ["так точно", "roger", "wilco", "понял", "copy", "выполня", "принял"]
        report_kw = ["здесь", "this is", "наблюдаем", "обнаружен", "потери", "контакт",
                     "находимся", "spotted", "taking fire", "casualties"]

        classification = MessageClassification.unclear
        order_type = None

        # ── Question detection ──────────────────────────────
        # Questions like "Почему не наводите?" / "Why aren't you..." are NOT commands.
        # They're either status requests or unclear messages that need LLM escalation.
        _question_words_ru = ["почему", "зачем", "отчего", "разве", "неужели",
                              "как так", "как же", "что за", "с какой стати"]
        _question_words_en = ["why", "how come", "what the", "why aren't", "why isn't",
                              "why don't", "why not"]
        _is_question = (
            any(text_lower.startswith(q) or f" {q} " in text_lower or f" {q}?" in text_lower
                for q in _question_words_ru + _question_words_en)
            or (text.strip().endswith("?") and any(
                q in text_lower for q in _question_words_ru + _question_words_en))
        )

        # Strong sender-identification signals → status report or ack
        is_self_report = any(kw in text_lower for kw in ["здесь ", "this is "])

        # Pre-check: does the message contain any command keywords?
        has_command_kw = any(kw in text_lower for kw in command_kw_en + command_kw_ru)
        has_ack_kw = any(kw in text_lower for kw in ack_kw)

        # Questions override command detection — classify as status_request or unclear
        if _is_question:
            if any(kw in text_lower for kw in status_req_kw):
                classification = MessageClassification.status_request
                order_type = "report_status"
            else:
                # "Почему не стреляете?" → unclear, low confidence → LLM escalation
                classification = MessageClassification.unclear
        # If message has BOTH ack AND command keywords, it's a command with ack preamble
        # e.g. "Вас понял. Атакуйте." or "Roger. Move to grid B8."
        elif has_ack_kw and has_command_kw:
            classification = MessageClassification.command
        elif has_ack_kw:
            classification = MessageClassification.acknowledgment
        elif any(kw in text_lower for kw in status_req_kw):
            classification = MessageClassification.status_request
            order_type = "report_status"
        elif is_self_report and any(kw in text_lower for kw in report_kw):
            # "Здесь [unit]. ..." is almost always a status report
            classification = MessageClassification.status_report
        elif any(kw in text_lower for kw in report_kw) and not has_command_kw:
            classification = MessageClassification.status_report
        elif has_command_kw:
            classification = MessageClassification.command

        # Determine order type for all command-classified messages
        if classification == MessageClassification.command:
            # ── Standby / ready-for-support detection ──
            # "Get ready for fire support on request" / "Будьте готовы к огневой поддержке по запросу"
            # should NOT be an immediate fire order — unit should stand by (observe)
            _standby_kw = [
                "get ready", "stand by", "standby", "be ready", "on request",
                "on call", "when called", "when requested", "prepare to support",
                "ready to support", "prepare for support",
                "готовность", "готовьтесь", "будьте готовы", "по запросу",
                "по вызову", "по команде", "ожидайте", "ждите",
                "приготовьтесь", "приготовиться", "в готовности",
            ]
            _is_standby = any(kw in text_lower for kw in _standby_kw)

            # ── Coordination detection ──
            # "Lead the attack", "coordinate artillery support" — these are attack orders
            # with coordination intent, NOT fire orders for the receiving unit.
            _coordination_kw = [
                "coordinate", "lead the", "lead attack", "coordinate with",
                "coordinate the", "organize the", "direct the", "command the",
                "координируй", "возглав", "руковод", "организуй атаку",
                "координируй огонь", "координируй поддержку",
                "наведите", "наведи", "свяжитесь", "свяжись",
                "запросите огонь", "запроси огонь", "вызовите огонь", "вызови огонь",
            ]
            _is_coordination = any(kw in text_lower for kw in _coordination_kw)
            
            # ── Request fire detection ──
            # "Request fire support", "Call for fire", "Direct artillery at enemy"
            # Unit should create a fire request to CoC artillery, not attack themselves
            _request_fire_kw = [
                "request fire", "call for fire", "request artillery", "call artillery",
                "direct artillery", "need fire support", "need artillery",
                "наведите артиллерию", "наведи артиллерию",
                "наводите артиллерию", "наводи артиллерию",
                "наведите миномёт", "наведи миномёт",
                "наводите миномёт", "наводи миномёт",
                "наведите миномет", "наведи миномет",
                "наводите миномет", "наводи миномет",
                "наведите огонь", "наведи огонь",
                "наводите огонь", "наводи огонь",
                "запросите огонь", "запроси огонь",
                "вызовите огонь", "вызови огонь",
                "свяжитесь с артиллерией", "свяжись с артиллерией",
                "свяжитесь с миномёт", "свяжись с миномёт",
                "свяжитесь с mortar", "свяжись с mortar",
                "артиллерию на цель", "миномёт на цель", "миномет на цель",
                "артиллерию на противника", "миномёт на противника",
            ]
            # Also detect "наведите ... на цель" / "наводите ... на цель" patterns
            # even if "артиллерию/миномёт" is not right after
            if not _is_request_fire:
                _navedi_pattern = any(kw in text_lower for kw in [
                    "наведите", "наведи", "наводите", "наводи",
                ])
                _on_target = any(kw in text_lower for kw in [
                    "на цель", "на противника", "на врага", "на позиции",
                    "на позицию противника", "at the target", "at target", "on target",
                    "at the enemy", "at enemy", "on the enemy",
                ])
                if _navedi_pattern and _on_target:
                    _is_request_fire = True
            _is_request_fire = any(kw in text_lower for kw in _request_fire_kw)

            # Determine order type — logic order matters!
            # 1. Standby for support → observe
            # 2. Request fire from CoC artillery → request_fire
            # 3. Coordination orders that mention attack → attack (not fire)
            # 4. Direct fire commands → fire
            # 5. Attack keywords → attack
            # 6. Move keywords → move
            # etc.
            if _is_standby and any(kw in text_lower for kw in [
                "fire support", "support fire", "artillery support",
                "огневая поддержк", "артподдержк", "поддержк огн",
                "fire", "огонь", "огневой"
            ]):
                # Standby for fire support → observe/wait, NOT immediate fire
                order_type = "observe"
            elif _is_request_fire:
                # "Request artillery support", "Direct artillery at enemy" → request_fire
                # This creates a fire request to CoC artillery
                order_type = "request_fire"
            elif _is_coordination and any(kw in text_lower for kw in ["attack", "assault", "engage",
                                                                       "атак", "штурм", "наступ"]):
                # "Lead the attack", "coordinate the attack" → attack with coordination
                order_type = "attack"
            elif any(kw in text_lower for kw in ["fire at", "fire on", "fire mission", "shoot at",
                                                 "огонь по", "огонь на", "открыть огонь", "стреляй",
                                                 "огонь по цели", "огонь по целям",
                                                 "огонь на цель", "откройте огонь"]):
                # Direct fire commands → fire
                order_type = "fire"
            elif any(kw in text_lower for kw in ["suppress", "подавить", "подавляющ",
                                                 "suppressive", "подавляющий"]):
                # Suppression orders → fire
                order_type = "fire"
            elif not _is_coordination and any(kw in text_lower for kw in [
                "artillery support", "fire support", "support fire",
                "need support on", "support on",
                "артподдержк", "огневая поддержк", "поддержк огн"
            ]):
                # "Request artillery support on grid X" → fire (but not if it's coordination)
                order_type = "fire"
            elif any(kw in text_lower for kw in ["hit any", "hit enemy", "engage any",
                                                   "fire on any", "open fire",
                                                   "бей по", "поразить", "удар по",
                                                   "огонь по любым", "огонь по всем"]):
                # "Hit any enemy target in your sight" → engage (fire at targets of opportunity)
                order_type = "attack"
                engagement_rules = "fire_at_will"
            # Check attack/eliminate BEFORE move: "Move to X. Eliminate enemy" should be attack
            elif any(kw in text_lower for kw in ["attack", "engage", "eliminate", "destroy", "neutralize",
                                                    "атак",
                                                    "capture", "seize", "take", "occupy",
                                                    "уничтож", "ликвидир", "поразить", "поразите",
                                                    "захвати", "захват", "овладе", "занять", "займ"]):
                order_type = "attack"
            elif any(kw in text_lower for kw in ["move", "advance", "form ", "выдвигай", "двигай",
                                                     "движен", "марш", "обход", "перестрои", "построение"]):
                order_type = "move"
            elif any(kw in text_lower for kw in ["defend", "hold", "оборон", "удержи"]):
                order_type = "defend"
            elif any(kw in text_lower for kw in ["observe", "наблюда"]):
                order_type = "observe"
            elif any(kw in text_lower for kw in ["disengage", "break contact", "разорвать контакт",
                                                   "разорви контакт", "выйти из боя", "выйди из боя",
                                                   "отцепи"]):
                order_type = "disengage"
            elif any(kw in text_lower for kw in ["withdraw", "retreat", "отход", "отступ"]):
                order_type = "withdraw"
            elif any(kw in text_lower for kw in ["halt", "stop", "стой", "стоп"]):
                order_type = "halt"
            elif any(kw in text_lower for kw in [
                "resupply", "re-supply", "rearm", "reload", "replenish",
                "пополн", "боеприпас", "снабж", "перезаряд", "дозаправ",
                "боекомплект", "пополнить бк", "пополни бк",
                "ammunition", "ammo", "supply point",
                "к складу", "на склад", "ближайш снабж",
                "nearest supply", "supply cache",
            ]):
                order_type = "resupply"

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

        # Match height/elevation references: "height 170", "высота 170", "выс. 250"
        height_pattern = re.compile(
            r'(?:height|hill|elevation|высот[аыеу]|выс\.?|отм\.?|отметк[аеу])\s*(\d+(?:\.\d+)?)',
            re.IGNORECASE,
        )
        for m in height_pattern.finditer(text):
            height_val = m.group(1)
            height_norm = f"height {height_val}"
            if not any(lr["normalized"] == height_norm for lr in location_refs):
                location_refs.append({
                    "source_text": m.group().strip(),
                    "ref_type": "height",
                    "normalized": height_norm,
                })

        # Match named map objects: airfield, bridge, hospital, fuel depot, etc.
        obj_pattern = re.compile(
            r'\b(airfield|bridge|fuel\s+depot|hospital|command\s+post|supply\s+cache'
            r'|observation\s+tower|pillbox|roadblock|bunker'
            r'|аэродром|мост|госпиталь|медпункт|склад|заправк\w*'
            r'|кп|командный\s+пункт|вышк\w*|дот|дзот|блокпост)\b',
            re.IGNORECASE,
        )
        for m in obj_pattern.finditer(text):
            obj_text = m.group().strip()
            if not any(lr["source_text"].lower() == obj_text.lower() for lr in location_refs):
                location_refs.append({
                    "source_text": obj_text,
                    "ref_type": "map_object",
                    "normalized": obj_text.lower(),
                })

        # ── Implicit target from "на цель" / "по цели" / "at the target" ──
        # When no explicit location is given but text says "at the target" / "на цель",
        # this means "at the enemy we've been tracking" → resolve from nearest contact.
        _target_phrases_ru = ["на цель", "по цели", "по целям", "на противника",
                              "по противнику", "на врага", "по врагу",
                              "на позицию противника", "по позиции противника"]
        _target_phrases_en = ["at the target", "at target", "on target", "at the enemy",
                              "on the enemy", "at enemy position", "on enemy position"]
        _has_explicit_location = any(lr["ref_type"] in ("snail", "grid", "coordinate", "height")
                                     for lr in location_refs)
        if not _has_explicit_location:
            _target_match = None
            for phrase in _target_phrases_ru + _target_phrases_en:
                if phrase in text_lower:
                    _target_match = phrase
                    break
            if _target_match:
                location_refs.append({
                    "source_text": _target_match,
                    "ref_type": "contact_target",
                    "normalized": "nearest_enemy_contact",
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
        # Speed detection — comprehensive EN/RU
        speed = None
        # Slow/cautious patterns
        slow_kw = [
            "slow", "careful", "cautious", "stealth", "stealthy", "quiet", "sneak",
            "tactical movement", "slow and careful", "carefully", "move slow",
            "cautiously", "silently", "covertly", "low profile",
            "медленн", "осторожн", "скрытно", "тихо", "крадучись", "без шума",
            "тактическ", "аккуратн", "не спеша", "осмотрительн",
            "перебежк", "ползком", "незаметн", "потихоньку",
            "с осторожн", "без лишнего шума", "скрытн",
        ]
        # Fast/rapid patterns
        fast_kw = [
            "fast", "rapid", "quick", "urgent", "rush", "sprint", "hurry", "double time",
            "move fast", "quickly", "asap", "full speed", "at speed",
            "at the double", "on the double", "forced march", "move out now",
            "срочно", "быстр", "немедленно", "бегом", "марш-бросок",
            "рывк", "скорее", "живее", "на скорости", "стремительн",
            "максимальн", "ускоренн", "галопом", "на рысях",
            "форсированн", "полным ходом", "мигом", "давай давай",
        ]
        if any(kw in text_lower for kw in slow_kw):
            speed = "slow"
        elif any(kw in text_lower for kw in fast_kw):
            speed = "fast"

        # ── Formation detection ─────────────────────────────────
        formation = None
        formation_map = {
            # English
            "column": "column", "single file": "column", "file": "column",
            "march column": "column", "road march": "column",
            "line": "line", "skirmish line": "line", "assault line": "line",
            "firing line": "line", "extended line": "line", "on line": "line",
            "abreast": "line",
            "wedge": "wedge", "vee": "vee", "v formation": "vee",
            "arrowhead": "wedge",
            "echelon left": "echelon_left", "echelon right": "echelon_right",
            "diamond": "diamond", "box": "box", "staggered column": "staggered",
            "staggered": "staggered",
            "herringbone": "herringbone",
            # Russian
            "колонн": "column", "походн": "column", "гуськом": "column",
            "в затылок": "column", "друг за другом": "column",
            "цепь": "line", "цепью": "line", "развернут": "line", "в линию": "line",
            "шеренг": "line", "в шеренг": "line", "пеленг": "line",
            "рассредоточ": "line", "стрелковая цепь": "line",
            "боевая линия": "line",
            "клин": "wedge", "клином": "wedge",
            "уступ влево": "echelon_left", "уступом влево": "echelon_left",
            "уступ вправо": "echelon_right", "уступом вправо": "echelon_right",
            "уступ": "echelon_right",  # default echelon direction
            "ромб": "diamond", "ромбом": "diamond",
            "каре": "box",
            "ёлочк": "herringbone", "елочк": "herringbone",
            "боевой порядок": "wedge",  # generic "combat formation" → default wedge
        }
        # Check formation patterns (check longer patterns first)
        for pattern, form_name in sorted(formation_map.items(), key=lambda x: -len(x[0])):
            if pattern in text_lower:
                formation = form_name
                break

        # Also look for explicit formation commands
        import re as _re
        form_cmd = _re.search(
            r'(?:form|formation|adopt|form up|go to|switch to|'
            r'построение|движение|двигаться|перестрои|принять|'
            r'в порядке|боевой порядок|марш\w*\s*порядок)\s*[:=]?\s*'
            r'(column|line|wedge|vee|diamond|echelon|herringbone|staggered|box|abreast|'
            r'колонн\w*|цеп\w*|клин\w*|уступ\w*|ромб\w*|каре|'
            r'шеренг\w*|пеленг\w*|походн\w*)',
            text_lower,
        )
        if form_cmd and not formation:
            matched_form = form_cmd.group(1).strip()
            for pattern, form_name in formation_map.items():
                if pattern in matched_form:
                    formation = form_name
                    break

        # Engagement rules
        engagement_rules = None
        if any(kw in text_lower for kw in ["hold fire", "не стрелять", "огонь не открывать"]):
            engagement_rules = "hold_fire"
        elif any(kw in text_lower for kw in ["fire at will", "огонь по готовности",
                                               "in your sight", "в поле зрения", "в зоне видимости",
                                               "any target", "any enemy", "по любым целям",
                                               "по всем целям", "по обнаруженным"]):
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
            formation=formation,
            engagement_rules=engagement_rules,
            confidence=self._compute_keyword_confidence(
                classification, order_type, location_refs,
                target_unit_refs, speed, engagement_rules, formation,
                original_text=text,
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
        formation: str | None = None,
        original_text: str = "",
    ) -> float:
        """
        Compute confidence score for keyword-parsed result.

        Higher confidence when more elements are successfully extracted.
        This drives the 3-tier model routing decision.

        Complex or ambiguous commands should have LOW confidence to trigger LLM parsing.
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
            if formation:
                conf += 0.03

            # ── Reduce confidence for complex/ambiguous commands ──
            # Multi-verb commands (move + attack, advance + eliminate, etc.) should
            # be sent to LLM for proper intent resolution.
            text_lower = original_text.lower()

            # Check for multiple action verbs in the same command
            move_verbs = ["move", "advance", "proceed", "выдвигай", "двигай", "марш", "движен"]
            attack_verbs = ["attack", "engage", "eliminate", "destroy", "neutralize",
                           "fire", "shoot", "suppress",
                           "атак", "уничтож", "ликвидир", "поразить", "огонь", "подавить"]
            defend_verbs = ["defend", "hold", "оборон", "удержи"]
            observe_verbs = ["observe", "recon", "scout", "наблюда", "разведк"]
            # Coordination/leadership verbs indicate complex multi-unit orders
            coord_verbs = ["coordinate", "lead", "organize", "direct", "command",
                          "координируй", "организуй", "возглав", "руковод", "командуй"]

            has_move = any(v in text_lower for v in move_verbs)
            has_attack = any(v in text_lower for v in attack_verbs)
            has_defend = any(v in text_lower for v in defend_verbs)
            has_observe = any(v in text_lower for v in observe_verbs)
            has_coord = any(v in text_lower for v in coord_verbs)

            verb_count = sum([has_move, has_attack, has_defend, has_observe])

            if verb_count >= 2:
                # Multiple action verbs → complex command → reduce confidence
                # This ensures LLM parses the intent correctly
                conf = min(conf, 0.65)  # cap at 0.65 to trigger nano model

            # Coordination orders are inherently complex — always send to LLM
            if has_coord:
                conf = min(conf, 0.50)  # cap at 0.50 to trigger full model

            # Long commands (>50 chars) are more likely to be complex
            if len(original_text) > 50:
                conf -= 0.05

            # Commands with multiple sentences are complex
            if original_text.count('.') >= 2:
                conf -= 0.10

        return min(max(conf, 0.15), 0.95)


# Singleton
order_parser = OrderParser()



