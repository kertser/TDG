"""
OrderParser – classifies radio messages and extracts structured data.

LLM routing strategy:
  - Non-commands (acks, status reports, status requests) → skip LLM (cheap)
  - Commands / unclear → ALWAYS call LLM for proper parsing
  - Model tier for commands: keyword confidence drives nano vs full model choice

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
    build_system_prompt,
    build_user_message,
    build_unit_roster,
    build_grid_info,
)
from backend.prompts.tactical_doctrine import get_tactical_doctrine

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

# Confidence thresholds for model routing (commands ALWAYS go to LLM)
CONF_SKIP_LLM = 0.95    # only non-commands (acks, reports) can skip LLM
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

    @staticmethod
    def _has_status_request_frame(text: str) -> bool:
        """Detect whether a message is asking for information rather than issuing an order."""
        text_lower = (text or "").lower().strip()
        if not text_lower:
            return False
        if text_lower.startswith("как только"):
            return False
        request_openers = [
            "доложи", "доложите", "сообщи", "сообщите", "опиши", "опишите",
            "какая", "какой", "какие", "каково", "сколько", "кто", "где",
            "what", "what's", "who", "where", "which", "describe", "report",
            "how far", "distance to",
        ]
        return (
            text_lower.endswith("?")
            or any(text_lower.startswith(opener) for opener in request_openers)
            or any(f" {opener} " in text_lower for opener in request_openers)
        )

    @staticmethod
    def _has_command_frame(text: str) -> bool:
        """Heuristic for imperative command phrasing."""
        text_lower = (text or "").lower()
        command_markers = [
            "выдвигай", "двигай", "обход", "охват", "заносите фланг",
            "свяжись", "свяжитесь", "скоординируй", "скоординируйте",
            "договорись", "договоритесь", "наведи", "наведите",
            "открывай огонь", "откройте огонь", "выполняй", "выполняйте",
            "move", "advance", "attack", "engage", "coordinate", "contact",
            "call for fire", "request fire", "direct fire", "lead",
        ]
        return any(marker in text_lower for marker in command_markers)

    @staticmethod
    def _has_explicit_fire_request_signal(text: str) -> bool:
        """Detect strong call-for-fire / fire-direction language."""
        text_lower = (text or "").lower()
        fire_request_markers = [
            "request fire", "call for fire", "request artillery", "call artillery",
            "direct artillery", "direct fire",
            "наведите артиллерию", "наведи артиллерию",
            "наведите миномёт", "наведи миномёт",
            "наведите миномет", "наведи миномет",
            "наведите огонь", "наведи огонь",
            "запросите огонь", "запроси огонь",
            "вызовите огонь", "вызови огонь",
            "артиллерию на цель", "миномёт на цель", "миномет на цель",
        ]
        if any(marker in text_lower for marker in fire_request_markers):
            return True
        return (
            any(marker in text_lower for marker in ["наведите", "наведи", "наводите", "наводи"])
            and any(marker in text_lower for marker in ["на цель", "по цели", "по противнику", "на противника"])
        )

    @staticmethod
    def _infer_doctrine_topics(original_text: str) -> list[str]:
        """Select only the doctrinal slices relevant to this order family."""
        text_lower = (original_text or "").lower()
        topics = ["general"]

        topic_keywords = {
            "offense": [
                "attack", "assault", "advance", "flank", "bound", "engage",
                "атак", "наступ", "охват", "обход", "штурм", "перебежк",
            ],
            "defense": [
                "defend", "hold", "screen", "delay", "withdraw",
                "оборон", "удерж", "сдержива", "отход", "отступ",
            ],
            "fires": [
                "fire", "artillery", "mortar", "call for fire", "smoke",
                "огонь", "артилл", "мином", "дым", "подав",
            ],
            "recon": [
                "recon", "observe", "screen", "drone", "uav",
                "развед", "наблюд", "бпла", "прикрой фланг наблюдением",
            ],
            "engineers": [
                "engineer", "breach", "mine", "bridge", "construct", "dig in",
                "сап", "инженер", "размини", "мин", "мост", "окоп", "укреп",
            ],
            "logistics": [
                "logistics", "resupply", "rearm", "ammo", "supply",
                "логист", "снабж", "боеприпас", "бк", "пополн",
            ],
            "aviation": [
                "aviation", "air", "helicopter", "medevac", "casevac", "airlift", "insert", "extract",
                "авиац", "вертол", "эвакуац", "десант", "высад",
            ],
            "map_objects": [
                "bridge", "roadblock", "minefield", "wire", "bunker", "smoke",
                "мост", "блокпост", "минное поле", "провол", "дот", "дым",
            ],
            "split_merge": [
                "split", "detach", "break into", "merge", "join up", "combine",
                "раздел", "выдел", "отдели", "слей", "объедин", "соединись",
            ],
        }
        for topic, keywords in topic_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                topics.append(topic)

        return list(dict.fromkeys(topics))

    def _reconcile_llm_result(
        self,
        original_text: str,
        llm_result: ParsedOrderData | None,
        keyword_result: ParsedOrderData,
    ) -> ParsedOrderData | None:
        """
        Keep strong imperative command signals from being downgraded by the LLM.
        """
        if llm_result is None:
            return None

        strong_command = (
            self._has_command_frame(original_text)
            and not self._has_status_request_frame(original_text)
        )
        strong_fire_request = self._has_explicit_fire_request_signal(original_text)

        if (
            strong_command
            and keyword_result.classification == MessageClassification.command
            and llm_result.classification != MessageClassification.command
        ):
            return keyword_result.model_copy(
                update={
                    "language": llm_result.language,
                    "confidence": max(keyword_result.confidence, 0.7),
                    "ambiguities": [
                        *(llm_result.ambiguities or []),
                        "LLM non-command overridden by strong command phrasing",
                    ],
                }
            )

        if (
            strong_fire_request
            and llm_result.classification == MessageClassification.command
            and keyword_result.order_type is not None
            and keyword_result.order_type.value == "request_fire"
            and (
                llm_result.order_type is None
                or llm_result.order_type.value != "request_fire"
            )
        ):
            merged_coord_refs = list(llm_result.coordination_unit_refs or [])
            for ref in keyword_result.coordination_unit_refs or []:
                if ref not in merged_coord_refs:
                    merged_coord_refs.append(ref)
            merged_location_refs = list(llm_result.location_refs or [])
            if not merged_location_refs and keyword_result.location_refs:
                merged_location_refs = list(keyword_result.location_refs)
            return llm_result.model_copy(
                update={
                    "order_type": keyword_result.order_type,
                    "location_refs": merged_location_refs,
                    "coordination_unit_refs": merged_coord_refs,
                    "coordination_kind": (
                        llm_result.coordination_kind
                        or keyword_result.coordination_kind
                        or "fire_support"
                    ),
                    "maneuver_kind": (
                        llm_result.maneuver_kind
                        or keyword_result.maneuver_kind
                    ),
                    "maneuver_side": (
                        llm_result.maneuver_side
                        or keyword_result.maneuver_side
                    ),
                    "confidence": max(llm_result.confidence, 0.72),
                }
            )

        return llm_result

    async def parse(
        self,
        original_text: str,
        units: list[dict],
        grid_info: dict | None = None,
        game_time: str = "",
        issuer_side: str | None = None,
        force_full_model: bool = False,
        # ── Enriched context (optional, injected into LLM prompt) ──
        terrain_context: str = "",
        contacts_context: str = "",
        objectives_context: str = "",
        friendly_status_context: str = "",
        environment_context: str = "",
        orders_context: str = "",
        radio_context: str = "",
        reports_context: str = "",
        map_objects_context: str = "",
    ) -> ParsedOrderData:
        """
        Parse a radio message into structured data.

        Routing depends on LLM_PARSING_MODE config:
          "llm_first"     — always call LLM (nano by default); keyword only as fallback
          "keyword_first"  — legacy 3-tier: keyword→nano→full
          "keyword_only"   — no LLM calls at all

        If force_full_model=True, always use the full model regardless of mode.
        """
        # ── Step 1: Always run keyword parser (used as hint or fallback) ──
        keyword_result = self._fallback_parse(original_text)

        parsing_mode = settings.LLM_PARSING_MODE

        # ── Step 2: If forced full model, skip all routing ──
        if force_full_model:
            if settings.OPENAI_API_KEY:
                logger.info("OrderParser: force_full_model — using %s", settings.OPENAI_MODEL)
                result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=self._get_client(),
                    model=settings.OPENAI_MODEL,
                    issuer_side=issuer_side,
                    terrain_context=terrain_context,
                    contacts_context=contacts_context,
                    objectives_context=objectives_context,
                    friendly_status_context=friendly_status_context,
                    environment_context=environment_context,
                    orders_context=orders_context,
                    radio_context=radio_context,
                    reports_context=reports_context,
                    map_objects_context=map_objects_context,
                )
                result = self._reconcile_llm_result(original_text, result, keyword_result)
                return result if result else keyword_result
            # No cloud key — try local model for force_full_model too
            local_client = self._get_local_client()
            if local_client:
                logger.info("OrderParser: force_full_model — no API key, using local model at %s",
                            settings.LOCAL_MODEL_URL)
                result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=local_client,
                    model=settings.LOCAL_MODEL_NAME,
                    issuer_side=issuer_side,
                    terrain_context=terrain_context,
                    contacts_context=contacts_context,
                    objectives_context=objectives_context,
                    friendly_status_context=friendly_status_context,
                    environment_context=environment_context,
                    orders_context=orders_context,
                    radio_context=radio_context,
                    reports_context=reports_context,
                    map_objects_context=map_objects_context,
                )
                result = self._reconcile_llm_result(original_text, result, keyword_result)
                return result if result else keyword_result
            return keyword_result

        # ── Step 3: keyword_only mode → return keyword result immediately ──
        if parsing_mode == "keyword_only":
            logger.info(
                "OrderParser: keyword_only mode. class=%s conf=%.2f",
                keyword_result.classification.value, keyword_result.confidence,
            )
            return keyword_result

        # ── Step 4: No API key → try local model → fall back to keyword ──
        if not settings.OPENAI_API_KEY:
            local_client = self._get_local_client()
            if local_client:
                logger.info("OrderParser: no API key, using local model at %s",
                            settings.LOCAL_MODEL_URL)
                result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=local_client,
                    model=settings.LOCAL_MODEL_NAME,
                    issuer_side=issuer_side,
                    terrain_context=terrain_context,
                    contacts_context=contacts_context,
                    objectives_context=objectives_context,
                    friendly_status_context=friendly_status_context,
                    environment_context=environment_context,
                    orders_context=orders_context,
                    radio_context=radio_context,
                    reports_context=reports_context,
                    map_objects_context=map_objects_context,
                )
                result = self._reconcile_llm_result(original_text, result, keyword_result)
                return result if result else keyword_result
            else:
                logger.warning("OrderParser: no API key and no local model — using keyword result")
                return keyword_result

        # ── Step 5: LLM-first mode (default) ──
        if parsing_mode == "llm_first":
            # Use keyword hints to pick model tier — or skip LLM entirely
            kw_conf = keyword_result.confidence
            kw_class = keyword_result.classification

            # Skip LLM ONLY for non-command classifications that are clearly identified.
            # Commands ALWAYS go to LLM regardless of keyword confidence.
            _is_non_command = kw_class in (
                MessageClassification.acknowledgment,
                MessageClassification.status_report,
                MessageClassification.status_request,
            )
            if _is_non_command and kw_conf >= CONF_SKIP_LLM:
                logger.info(
                    "OrderParser[llm_first]: non-command class=%s conf=%.2f — skipping LLM",
                    kw_class.value, kw_conf,
                )
                return keyword_result

            if kw_conf >= 0.70 and kw_class != MessageClassification.unclear:
                model = settings.OPENAI_MODEL_NANO
                tier = "nano"
            else:
                model = settings.OPENAI_MODEL
                tier = "full"

            logger.info(
                "OrderParser[llm_first]: keyword hint class=%s conf=%.2f → using %s (%s)",
                keyword_result.classification.value, kw_conf, tier, model,
            )

            result = await self._call_llm(
                original_text, units, grid_info, game_time,
                client=self._get_client(),
                model=model,
                issuer_side=issuer_side,
                terrain_context=terrain_context,
                contacts_context=contacts_context,
                objectives_context=objectives_context,
                friendly_status_context=friendly_status_context,
                environment_context=environment_context,
                orders_context=orders_context,
                radio_context=radio_context,
                reports_context=reports_context,
                map_objects_context=map_objects_context,
            )
            result = self._reconcile_llm_result(original_text, result, keyword_result)

            # If nano returned unclear, escalate to full model
            if result and result.classification == MessageClassification.unclear and tier != "full":
                logger.info(
                    "OrderParser[llm_first]: %s classified unclear → escalating to full (%s)",
                    tier, settings.OPENAI_MODEL,
                )
                full_result = await self._call_llm(
                    original_text, units, grid_info, game_time,
                    client=self._get_client(),
                    model=settings.OPENAI_MODEL,
                    issuer_side=issuer_side,
                    terrain_context=terrain_context,
                    contacts_context=contacts_context,
                    objectives_context=objectives_context,
                    friendly_status_context=friendly_status_context,
                    environment_context=environment_context,
                    orders_context=orders_context,
                    radio_context=radio_context,
                    reports_context=reports_context,
                    map_objects_context=map_objects_context,
                )
                full_result = self._reconcile_llm_result(original_text, full_result, keyword_result)
                if full_result and full_result.classification != MessageClassification.unclear:
                    return full_result
                if full_result:
                    return full_result

            return result if result else keyword_result

        # ── Step 6: keyword_first mode (legacy 3-tier) ──
        # Skip LLM ONLY for non-command classifications (acks, reports)
        # Commands ALWAYS go through LLM.
        _is_non_command_kf = keyword_result.classification in (
            MessageClassification.acknowledgment,
            MessageClassification.status_report,
            MessageClassification.status_request,
        )
        if _is_non_command_kf and keyword_result.confidence >= CONF_SKIP_LLM:
            logger.info(
                "OrderParser[keyword_first]: non-command class=%s conf=%.2f, skipping LLM",
                keyword_result.classification.value, keyword_result.confidence,
            )
            return keyword_result

        # Choose model tier based on keyword confidence
        if keyword_result.confidence >= CONF_USE_NANO:
            model = settings.OPENAI_MODEL_NANO
            tier = "nano"
        else:
            model = settings.OPENAI_MODEL
            tier = "full"

        logger.info(
            "OrderParser[keyword_first]: keyword confidence %.2f → using %s model (%s)",
            keyword_result.confidence, tier, model,
        )

        result = await self._call_llm(
            original_text, units, grid_info, game_time,
            client=self._get_client(),
            model=model,
            issuer_side=issuer_side,
            terrain_context=terrain_context,
            contacts_context=contacts_context,
            objectives_context=objectives_context,
            friendly_status_context=friendly_status_context,
            environment_context=environment_context,
            orders_context=orders_context,
            radio_context=radio_context,
            reports_context=reports_context,
            map_objects_context=map_objects_context,
        )
        result = self._reconcile_llm_result(original_text, result, keyword_result)

        # If unclear, escalate to full model
        if result and result.classification == MessageClassification.unclear and tier != "full":
            logger.info(
                "OrderParser[keyword_first]: %s classified unclear → escalating to full (%s)",
                tier, settings.OPENAI_MODEL,
            )
            full_result = await self._call_llm(
                original_text, units, grid_info, game_time,
                client=self._get_client(),
                model=settings.OPENAI_MODEL,
                issuer_side=issuer_side,
                terrain_context=terrain_context,
                contacts_context=contacts_context,
                objectives_context=objectives_context,
                friendly_status_context=friendly_status_context,
                environment_context=environment_context,
                orders_context=orders_context,
                radio_context=radio_context,
                reports_context=reports_context,
                map_objects_context=map_objects_context,
            )
            full_result = self._reconcile_llm_result(original_text, full_result, keyword_result)
            if full_result and full_result.classification != MessageClassification.unclear:
                return full_result
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
        terrain_context: str = "",
        contacts_context: str = "",
        objectives_context: str = "",
        friendly_status_context: str = "",
        environment_context: str = "",
        orders_context: str = "",
        radio_context: str = "",
        reports_context: str = "",
        map_objects_context: str = "",
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

        doctrine_topics = self._infer_doctrine_topics(original_text)
        system = build_system_prompt(
            get_tactical_doctrine("brief", topics=doctrine_topics)
        ).format(
            unit_roster=build_unit_roster(filtered_units),
            grid_info=build_grid_info(grid_info),
            game_time=game_time or "Unknown",
            height_tops_context=_build_height_tops_context(grid_info),
            terrain_context=terrain_context or "No terrain data available.",
            contacts_context=contacts_context or "No known enemy contacts.",
            objectives_context=objectives_context or "No specific objectives defined.",
            friendly_status_context=friendly_status_context or "No detailed status available.",
            environment_context=environment_context or "No environment data available.",
            orders_context=orders_context or "No prior own-side orders.",
            radio_context=radio_context or "No recent radio/chat traffic.",
            reports_context=reports_context or "No recent operational reports.",
            map_objects_context=map_objects_context or "No known map objects.",
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
                # Progressively strip unsupported params (some models reject
                # max_tokens, temperature, or both — e.g. gpt-5-nano).
                response = None
                for _param_attempt in range(3):
                    try:
                        response = await client.chat.completions.create(**create_kwargs)
                        break
                    except Exception as api_err:
                        err_str = str(api_err)
                        stripped = False
                        if ("max_tokens" in err_str or "max_completion_tokens" in err_str):
                            create_kwargs.pop("max_tokens", None)
                            create_kwargs.pop("max_completion_tokens", None)
                            stripped = True
                        if "temperature" in err_str:
                            create_kwargs.pop("temperature", None)
                            stripped = True
                        if not stripped:
                            raise
                if response is None:
                    raise RuntimeError(f"Failed to call {model} after stripping params")

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
                         "request artillery", "breach", "clear a lane", "clear lane",
                         "split", "split off", "detach", "merge with", "join up with", "combine with",
                         "lay mines", "mine the", "emplace mines", "deploy bridge",
                         "bridge the", "construct", "build", "dig in", "entrench",
                         "fortify", "establish command post", "set up aid station",
                         "airlift", "insert", "extract", "landing zone", "lz"]
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
                         "артиллерию на", "миномёт на", "минометн на",
                         "раздел", "выдел", "отдели", "слей", "объедин", "соединись",
                         "проделай проход", "проделайте проход", "разминир", "разминируй",
                         "сними заграждение", "минируй", "заминируй", "ставь мины",
                         "установи мины", "навести мост", "разверни мост", "оборудуй",
                         "окопай", "укрепи", "построй", "возведи", "подвези",
                         "снабди", "эвакуируй", "десант", "высад", "погруз", "выгруз"]
        status_req_kw = ["доложи", "report", "обстанов", "что у вас", "what's happening", "status"]
        status_req_focus_map = {
            "nearby_friendlies": [
                "кто рядом", "какие подразделения рядом", "какие части рядом",
                "какие силы рядом", "свои рядом", "friendly nearby", "friendlies nearby",
                "who is near you", "who's near you", "units near you", "nearby units",
            ],
            "terrain": [
                "местност", "опиши местност", "terrain", "ground around you",
                "describe terrain", "what terrain", "какая местность", "что за местность",
                "cover around you", "укрыти", "ground nearby",
            ],
            "enemy": [
                "противник", "enemy", "contact", "контакт", "кого видишь",
                "видишь противника", "enemy seen", "any enemy", "spot anything",
            ],
            "position": [
                "где ты", "где вы", "твоя позиция", "ваша позиция", "position",
                "where are you", "your location", "your grid", "координаты",
            ],
            "task": [
                "какая задача", "что делаешь", "что делаете", "чем занят",
                "current task", "what are you doing", "mission now", "orders now",
            ],
            "condition": [
                "состояни", "потери", "боеприпас", "бк", "мораль", "готовност",
                "condition", "readiness", "casualties", "ammo", "morale", "combat ready",
            ],
            "weather": [
                "погода", "видимость", "условия", "weather", "visibility", "conditions",
            ],
            "objects": [
                "объект", "препятств", "загражден", "строени", "map object",
                "obstacle", "structure", "bridge nearby",
            ],
            "road_distance": [
                "дорог", "road", "distance to road", "nearest road", "сколько до дороги",
                "дистанция до дороги", "как далеко до дороги",
            ],
        }
        ack_kw = ["так точно", "roger", "wilco", "понял", "copy", "выполня", "принял"]
        report_kw = ["здесь", "this is", "наблюдаем", "обнаружен", "потери", "контакт",
                     "находимся", "spotted", "taking fire", "casualties"]

        classification = MessageClassification.unclear
        order_type = None
        status_request_focus: list[str] = []
        coordination_unit_refs: list[str] = []
        coordination_kind = None
        maneuver_kind = None
        maneuver_side = None
        map_object_type = None

        def _infer_status_request_focus(raw_text: str) -> list[str]:
            inferred: list[str] = []
            raw_lower = raw_text.lower()
            for focus_name, patterns in status_req_focus_map.items():
                if any(pat in raw_lower for pat in patterns):
                    inferred.append(focus_name)
            return inferred or ["full"]

        has_status_request_frame = self._has_status_request_frame(text)

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
                status_request_focus = _infer_status_request_focus(text)
            elif any(
                any(pat in text_lower for pat in patterns)
                for patterns in status_req_focus_map.values()
            ):
                classification = MessageClassification.status_request
                order_type = "report_status"
                status_request_focus = _infer_status_request_focus(text)
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
            status_request_focus = _infer_status_request_focus(text)
        elif has_status_request_frame and any(
            any(pat in text_lower for pat in patterns)
            for patterns in status_req_focus_map.values()
        ):
            classification = MessageClassification.status_request
            order_type = "report_status"
            status_request_focus = _infer_status_request_focus(text)
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
                "артиллерию на цель", "миномёт на цель", "миномет на цель",
                "артиллерию на противника", "миномёт на противника",
            ]
            _is_request_fire = any(kw in text_lower for kw in _request_fire_kw)
            _liaison_only = (
                any(kw in text_lower for kw in [
                    "свяж", "координиру", "coordinate with", "link up with", "liaise with",
                ])
                and not any(kw in text_lower for kw in [
                    "огонь", "fire support", "support fire", "артподдержк",
                    "огневая поддержк", "на цель", "по цели", "противник", "enemy",
                    "suppress", "подав",
                ])
            )
            if _liaison_only:
                _is_request_fire = False
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

            _has_enemy_reference = any(kw in text_lower for kw in [
                "противник", "враг", "enemy", "contact", "contacts",
            ])
            _has_flank_maneuver = any(kw in text_lower for kw in [
                "обход", "охват", "flank", "envelop", "заносите фланг",
            ])
            _has_follow_maneuver = any(kw in text_lower for kw in [
                "follow ", "follow-", "trail ", "keep behind", "move behind",
                "следуй за", "следовать за", "иди за", "двигайся за",
                "держись за", "держаться за", "за ним", "за ними",
            ])
            _has_bounding_maneuver = any(kw in text_lower for kw in [
                "bound forward", "bounding", "leapfrog", "bounds by",
                "перебежк", "перекатами", "скачками", "bound by fire",
            ])
            _has_support_by_fire = any(kw in text_lower for kw in [
                "support by fire", "supporting fire position",
                "позиция поддержки огнем", "поддержка огнём", "поддержка огнем",
                "огневая позиция поддержки",
            ])
            _has_delay_mission = any(kw in text_lower for kw in [
                "delay them", "delay the enemy", "fighting withdrawal",
                "задерживай", "задерживайте", "сдерживай", "сдерживайте",
            ])
            _has_screen_mission = any(kw in text_lower for kw in [
                "screen the flank", "screen left flank", "screen right flank",
                "screen forward", "screen this axis",
                "прикрой фланг наблюдением", "прикрыть фланг наблюдением",
                "экранируй", "screen and report",
            ])
            _has_breach_order = any(kw in text_lower for kw in [
                "breach", "clear a lane", "clear lane", "open a lane",
                "проделай проход", "проделайте проход", "разминир", "разминируй",
                "сними заграждение", "очисти проход",
            ])
            _has_lay_mines_order = any(kw in text_lower for kw in [
                "lay mines", "mine the", "emplace mines", "set a minefield",
                "минируй", "заминируй", "ставь мины", "установи мины",
                "создай минное поле", "поставь минное поле",
            ])
            _has_deploy_bridge_order = any(kw in text_lower for kw in [
                "deploy bridge", "bridge the", "lay bridge", "launch bridge",
                "навести мост", "разверни мост", "развернуть мост",
                "мостоукладчик", "на переправе мост",
            ])
            _has_construct_order = any(kw in text_lower for kw in [
                "construct", "build", "dig in", "entrench", "fortify",
                "set up command post", "establish command post",
                "set up aid station", "set up supply point", "set up observation post",
                "оборудуй", "окопай", "окопаться", "укрепи", "построй", "возведи",
                "оборудуй позицию", "оборудуй окоп", "разверни кп", "разверни медпункт",
                "разверни пункт снабжения", "пост наблюдения",
            ])
            _has_smoke_fire_order = any(kw in text_lower for kw in [
                "fire smoke", "lay smoke", "deploy smoke", "screen with smoke",
                "put smoke", "smoke the", "set smoke",
                "поставь дым", "поставьте дым", "дымовую завесу", "прикрой дымом",
                "накрой дымом", "отстреляй дымами",
            ])
            _has_split_order = any(kw in text_lower for kw in [
                "split", "split off", "detach", "break into", "peel off",
                "раздел", "разделись", "выдели", "выделите", "отдели", "отделите",
            ])
            _has_merge_order = any(kw in text_lower for kw in [
                "merge with", "join up with", "combine with", "rejoin",
                "merge back", "слий", "слейтесь", "объедин", "соединись", "соединитесь",
            ])
            _has_air_mobility_order = any(kw in text_lower for kw in [
                "insert", "extract", "airlift", "landing zone", "lz",
                "casevac", "medevac", "pickup zone", "drop zone",
                "десант", "высад", "эвакуируй", "эвакуация", "забери раненых",
                "посадочная площадка", "площадка посадки",
            ])

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
            elif _has_split_order:
                order_type = "split"
            elif _has_merge_order:
                order_type = "merge"
            elif _has_breach_order:
                order_type = "breach"
            elif _has_lay_mines_order:
                order_type = "lay_mines"
            elif _has_deploy_bridge_order:
                order_type = "deploy_bridge"
            elif _has_construct_order:
                order_type = "construct"
            elif _has_smoke_fire_order and _is_request_fire:
                order_type = "request_fire"
            elif _has_smoke_fire_order:
                order_type = "fire"
            elif _is_request_fire:
                # "Request artillery support", "Direct artillery at enemy" → request_fire
                # This creates a fire request to CoC artillery
                order_type = "request_fire"
            elif _has_support_by_fire:
                order_type = "support"
                maneuver_kind = "support_by_fire"
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
            elif _is_coordination and coordination_unit_refs:
                order_type = "support"
            elif _has_flank_maneuver and _has_enemy_reference:
                order_type = "attack"
                maneuver_kind = "flank"
            elif _has_delay_mission:
                order_type = "disengage"
            # Check attack/eliminate BEFORE move: "Move to X. Eliminate enemy" should be attack
            elif any(kw in text_lower for kw in ["attack", "engage", "eliminate", "destroy", "neutralize",
                                                    "атак",
                                                    "capture", "seize", "take", "occupy",
                                                    "уничтож", "ликвидир", "поразить", "поразите",
                                                    "захвати", "захват", "овладе", "занять", "займ"]):
                order_type = "attack"
            elif _has_air_mobility_order:
                order_type = "move"
            elif any(kw in text_lower for kw in ["move", "advance", "form ", "выдвигай", "двигай",
                                                     "движен", "марш", "обход", "перестрои", "построение"]):
                order_type = "move"
                if _has_follow_maneuver:
                    maneuver_kind = "follow"
                elif _has_bounding_maneuver:
                    maneuver_kind = "bounding"
            elif any(kw in text_lower for kw in ["defend", "hold", "оборон", "удержи"]):
                order_type = "defend"
            elif _has_screen_mission or any(kw in text_lower for kw in ["observe", "наблюда", "recon", "развед"]):
                order_type = "observe"
            elif any(kw in text_lower for kw in ["disengage", "break contact", "разорвать контакт",
                                                    "разорви контакт", "выйти из боя", "выйди из боя",
                                                    "отцепи"]):
                order_type = "disengage"
            elif any(kw in text_lower for kw in ["withdraw", "retreat", "pull back", "fall back",
                                                    "отход", "отступ", "отойти", "отойд",
                                                    "отвести", "отводи",
                                                    "назад", "уходим", "уходи"]):
                order_type = "disengage"  # treat withdraw/retreat same as disengage
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
            r'|observation\s+tower|pillbox|roadblock|bunker|crossing|minefield'
            r'|trench|entrenchment|wire|smoke(?:\s+screen)?|anti-tank\s+ditch'
            r'|аэродром|мост|переправ\w*|госпиталь|медпункт|склад|заправк\w*'
            r'|кп|командный\s+пункт|вышк\w*|дот|дзот|блокпост|минн\w+\s+пол\w*'
            r'|окоп\w*|транше\w*|проволок\w*|дым\w*|противотанков\w+\s+ров\w*)\b',
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

        map_object_patterns = [
            ("at_minefield", ["anti-tank minefield", "at minefield", "противотанковое минное поле", "пт минное поле"]),
            ("minefield", ["minefield", "минное поле", "мины"]),
            ("barbed_wire", ["barbed wire", "wire obstacle", "колючая проволока", "проволочное заграждение"]),
            ("concertina_wire", ["concertina", "razor wire", "спираль бруно", "егоза"]),
            ("roadblock", ["roadblock", "checkpoint", "блокпост", "дорожное заграждение"]),
            ("anti_tank_ditch", ["anti-tank ditch", "tank ditch", "противотанковый ров"]),
            ("dragons_teeth", ["dragon's teeth", "dragons teeth", "надолбы", "зубы дракона"]),
            ("entrenchment", ["entrenchment", "trench", "foxhole", "окоп", "траншея", "укреплен"]),
            ("observation_tower", ["observation tower", "watchtower", "вышка", "наблюдательный пост"]),
            ("field_hospital", ["field hospital", "aid station", "медпункт", "полевой госпиталь"]),
            ("command_post_structure", ["command post", "hq post", "командный пункт", "кп"]),
            ("supply_cache", ["supply cache", "ammo dump", "supply point", "склад", "пункт снабжения"]),
            ("bridge_structure", ["bridge", "crossing", "мост", "переправа"]),
            ("pillbox", ["pillbox", "bunker", "дот", "дзот"]),
            ("smoke", ["smoke", "smokescreen", "дым", "дымовая завеса"]),
        ]
        for candidate_type, patterns in map_object_patterns:
            if any(pattern in text_lower for pattern in patterns):
                map_object_type = candidate_type
                break

        has_resolved_location = any(
            lr["ref_type"] in {"snail", "grid", "coordinate", "height", "map_object"}
            for lr in location_refs
        )
        implied_contact_target = (
            _has_enemy_reference
            or any(kw in text_lower for kw in [
                "на цель", "по цели", "цель", "at the target", "on target", "target",
            ])
        )
        if (
            classification == MessageClassification.command
            and order_type in ("attack", "fire", "request_fire")
            and not has_resolved_location
            and implied_contact_target
        ):
            location_refs.append({
                "source_text": "current contact",
                "ref_type": "contact_target",
                "normalized": "current_contact",
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
            elif (
                classification == MessageClassification.command
                and order_type in ("attack", "request_fire", "support")
                and any(kw in text_lower for kw in ["противник", "враг", "enemy", "contact"])
                and any(kw in text_lower for kw in ["обход", "охват", "flank", "envelop", "фланг", "fire", "огонь"])
            ):
                location_refs.append({
                    "source_text": "enemy contact",
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
            r'|infantry|mechanized|aviation|air|helicopter|drone|medevac)\s+'
            r'(?:team|section|platoon|company|squad|battery|group|flight|wing|pair)'
            r'(?:\s+\d+)?)\b',
            re.IGNORECASE,
        )
        for m in named_pat.finditer(text):
            ref = m.group().strip()
            if ref not in target_unit_refs:
                target_unit_refs.append(ref)

        # Custom callsign patterns: "A-squad", "C-squad", "Alpha", etc.
        callsign_pat = re.compile(
            r'\b([A-Za-z]-(?:squad|team|section|platoon|group))\b',
            re.IGNORECASE,
        )
        for m in callsign_pat.finditer(text):
            ref = m.group().strip()
            if ref not in target_unit_refs:
                target_unit_refs.append(ref)

        # Also match standalone known unit names (e.g. "Mortar" alone)
        standalone_pat = re.compile(
            r'\b(Mortar|Sniper|Recon|Artillery|HQ|Logistics|Aviation|Air|Drone|Medevac'
            r'|Штаб|Миномёт|Миномет|Разведка|Артиллерия|Авиация|Логистика|БПЛА)\b',
            re.IGNORECASE,
        )
        for m in standalone_pat.finditer(text):
            ref = m.group().strip()
            if ref not in target_unit_refs:
                target_unit_refs.append(ref)

        # Russian named: "разведгруппа", "миномётная секция"
        ru_named_pat = re.compile(
            r'\b((?:развед\w*|миномёт\w*|минометн\w*|танк\w*|снайпер\w*|сапёрн\w*'
            r'|артиллер\w*|инженерн\w*|логист\w*|авиац\w*|вертол[её]т\w*|бпла\w*)'
            r'\s*(?:группа|секция|взвод|рота|команда|звено|экипаж)?)\b',
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
        if not formation:
            if any(kw in text_lower for kw in ["левым охватом", "обход слева", "левый фланг"]):
                formation = "echelon_left"
                maneuver_side = "left"
            elif any(kw in text_lower for kw in ["правым охватом", "обход справа", "правый фланг"]):
                formation = "echelon_right"
                maneuver_side = "right"
        elif formation == "echelon_left":
            maneuver_side = maneuver_side or "left"
        elif formation == "echelon_right":
            maneuver_side = maneuver_side or "right"

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

        # ── Support target ref: extract the unit being supported in standby orders ──
        # e.g. "be ready to support C-squad's targets" → support_target_ref = "C-squad"
        # e.g. "Будьте готовы поддержать огнём по целям, которые вам передаст C-squad" → "C-squad"
        support_target_ref = None
        merge_target_ref = None
        split_ratio = None
        _is_standby_check = (classification == MessageClassification.command
                             and order_type == "observe"
                             and any(kw in text_lower for kw in [
                                 "get ready", "stand by", "standby", "be ready", "on request",
                                 "on call", "ready to support", "prepare to support",
                                 "готовность", "готовьтесь", "будьте готовы", "по запросу",
                                 "по вызову", "по команде", "в готовности",
                                 "support", "поддержать", "поддерж", "целям", "передаст",
                             ]))
        if _is_standby_check or order_type in ("observe", "support", "resupply", "request_fire"):
            # Look for patterns like "support X", "поддержать X", "targets from X",
            # "которые вам передаст X", "целям X", "work with X"
            import re as _re2
            support_patterns = [
                # EN: "support [unit]", "support [unit]'s targets"
                r'support\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon))?)',
                r"([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon))?)'s\s+(?:targets?|coordinates?|data)",
                r'targets?\s+from\s+([A-Za-z][\w-]+)',
                r'work\s+with\s+([A-Za-z][\w-]+)',
                r'coordinate\s+with\s+([A-Za-z][\w-]+)',
                r'resupply\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'rearm\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'escort\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                # RU: "поддержать X", "целям от X", "передаст X"
                r'поддержать\s+(?:огнём\s+)?(?:по\s+)?(?:целям[,\s]*)?(?:которые\s+)?(?:вам\s+)?(?:передаст|укажет|назначит)\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'поддержать\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'снабд(?:и|ить|ите)\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'подвез(?:и|ите)\s+(?:боеприпасы|бк|снабжение)?\s*(?:к|для)?\s*([A-Za-zА-Яа-яё][\w-]+)',
                r'передаст\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'укажет\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'целям\s+(?:от\s+)?([A-Za-zА-Яа-яё][\w-]+)',
                r'по\s+команде\s+([A-Za-zА-Яа-яё][\w-]+)',
            ]
            for pat in support_patterns:
                m2 = _re2.search(pat, text, _re2.IGNORECASE)
                if m2:
                    candidate = m2.group(1).strip().rstrip(".,;!")
                    # Don't pick up generic words as unit refs
                    generic = {"огнём", "fire", "support", "targets", "целям", "all", "any",
                               "the", "вам", "ваши", "your", "his", "their"}
                    if candidate.lower() not in generic and len(candidate) > 1:
                        support_target_ref = candidate
                        break

        if order_type == "merge":
            import re as _re_merge
            merge_patterns = [
                r'merge\s+with\s+([A-Za-z][\w-]+(?:\s+[A-Za-z][\w-]+)*)',
                r'join\s+up\s+with\s+([A-Za-z][\w-]+(?:\s+[A-Za-z][\w-]+)*)',
                r'combine\s+with\s+([A-Za-z][\w-]+(?:\s+[A-Za-z][\w-]+)*)',
                r'соедини(?:сь|тесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'объедини(?:сь|тесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'слей(?:ся|тесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
            ]
            for pat in merge_patterns:
                match = _re_merge.search(pat, text, _re_merge.IGNORECASE)
                if match:
                    merge_target_ref = match.group(1).strip().rstrip(".,;!")
                    break

        if order_type == "split":
            import re as _re_split
            ratio_patterns = [
                (r'(\d{1,2})\s*%', lambda m: max(0.1, min(0.9, int(m.group(1)) / 100.0))),
                (r'half|one half|половин', lambda _m: 0.5),
                (r'one third|third|треть', lambda _m: 1 / 3),
                (r'two thirds|две трети', lambda _m: 2 / 3),
                (r'quarter|четверт', lambda _m: 0.25),
            ]
            for pat, builder in ratio_patterns:
                match = _re_split.search(pat, text_lower, _re_split.IGNORECASE)
                if match:
                    split_ratio = round(builder(match), 2)
                    break

        # ── Coordination refs: units mentioned for liaison / support, not as recipients ──
        if classification == MessageClassification.command:
            import re as _re3

            coord_patterns = [
                r'coordinate\s+with\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'link\s+up\s+with\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'liaise\s+with\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'contact\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'work\s+with\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'свяж(?:ись|итесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'скоординиру(?:й|йте)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'договор(?:ись|итесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
            ]
            for pat in coord_patterns:
                for match in _re3.finditer(pat, text, _re3.IGNORECASE):
                    candidate = match.group(1).strip().rstrip(".,;!")
                    candidate = _re3.split(
                        r'\b(?:и|and|then|чтобы|for|to|with|с\s+ними)\b',
                        candidate,
                        maxsplit=1,
                        flags=_re3.IGNORECASE,
                    )[0].strip().rstrip(".,;!")
                    if candidate and candidate not in coordination_unit_refs:
                        coordination_unit_refs.append(candidate)

            follow_patterns = [
                r'follow\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'trail\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'keep\s+behind\s+([A-Za-z][\w-]+(?:\s+(?:team|squad|section|platoon|battery|group))?)',
                r'следуй\s+за\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'следовать\s+за\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'иди\s+за\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'двигай(?:ся)?\s+за\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'держ(?:ись|аться)\s+за\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
            ]
            for pat in follow_patterns:
                for match in _re3.finditer(pat, text, _re3.IGNORECASE):
                    candidate = match.group(1).strip().rstrip(".,;!")
                    candidate = _re3.split(
                        r'\b(?:и|and|then|keeping|maintaining|maintain|with|слева|справа|left|right)\b',
                        candidate,
                        maxsplit=1,
                        flags=_re3.IGNORECASE,
                    )[0].strip().rstrip(".,;!")
                    if candidate and candidate not in coordination_unit_refs:
                        coordination_unit_refs.append(candidate)
                        maneuver_kind = maneuver_kind or "follow"

            # If the text refers to "the mortars"/"artillery" generically, keep that context
            if any(kw in text_lower for kw in ["миномёт", "миномет", "mortar"]):
                if not any("мином" in ref.lower() or "mortar" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Mortar")
            elif any(kw in text_lower for kw in ["артиллер", "artillery"]):
                if not any("артилл" in ref.lower() or "artillery" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Artillery")

            # Do not confuse the addressed unit with the coordination partner.
            addressed_refs = {ref.lower() for ref in target_unit_refs}
            coordination_unit_refs = [
                ref for ref in coordination_unit_refs
                if ref.lower() not in addressed_refs
            ]

            if any(kw in text_lower for kw in [
                "прикры", "covering fire", "cover me", "cover your movement",
                "огневое прикрытие", "прикроют", "поддержат огн",
            ]):
                coordination_kind = "covering_fire"
            elif any(kw in text_lower for kw in [
                "огневая поддержк", "артподдержк", "fire support", "support fire",
                "suppress", "подав",
            ]):
                coordination_kind = "fire_support"
            elif coordination_unit_refs:
                coordination_kind = "coordination"

        if maneuver_kind == "flank" and maneuver_side is None:
            if any(kw in text_lower for kw in ["слева", "left flank", "left hook", "left envelopment"]):
                maneuver_side = "left"
            elif any(kw in text_lower for kw in ["справа", "right flank", "right hook", "right envelopment"]):
                maneuver_side = "right"

        if maneuver_kind is None and _has_follow_maneuver and coordination_unit_refs:
            maneuver_kind = "follow"
        if maneuver_kind is None and _has_flank_maneuver:
            maneuver_kind = "flank"
        if maneuver_kind is None and _has_bounding_maneuver:
            maneuver_kind = "bounding"
        if maneuver_kind is None and _has_support_by_fire:
            maneuver_kind = "support_by_fire"

        return ParsedOrderData(
            classification=classification,
            language=lang,
            target_unit_refs=target_unit_refs,
            sender_ref=sender_ref,
            order_type=order_type,
            status_request_focus=status_request_focus,
            location_refs=[LocationRefRaw(**lr) for lr in location_refs],
            speed=speed,
            formation=formation,
            engagement_rules=engagement_rules,
            support_target_ref=support_target_ref,
            merge_target_ref=merge_target_ref,
            split_ratio=split_ratio,
            map_object_type=map_object_type,
            coordination_unit_refs=coordination_unit_refs,
            coordination_kind=coordination_kind,
            maneuver_kind=maneuver_kind,
            maneuver_side=maneuver_side,
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

            # ── Boost for simple, unambiguous commands ──
            # If the command is short, has ONE verb, order_type, and a precise location,
            # the keyword parser fully understands it — boost to skip LLM.
            # Examples: "Move to F7-5-6", "Attack B8-3", "Defend at C4-2-1"
            if (verb_count <= 1 and order_type and not has_coord
                    and len(original_text) <= 50
                    and original_text.count('.') < 2):
                has_precise = any(
                    lr.get("ref_type") in ("snail", "coordinate", "height")
                    for lr in location_refs
                )
                # Boost if we have either a precise location or a locationless order
                # (halt, retreat, disengage don't need locations)
                no_loc_orders = {"halt", "disengage", "resupply", "observe"}
                if has_precise or order_type in no_loc_orders:
                    conf += 0.08

            # Commands must NEVER skip LLM — cap at 0.85 (well below CONF_SKIP_LLM=0.95)
            # This ensures every command goes through at least the nano model.
            conf = min(conf, 0.85)

        return min(max(conf, 0.15), 0.95)


# Singleton
order_parser = OrderParser()



