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
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256

from openai import AsyncOpenAI
from pydantic import ValidationError

from backend.config import settings
from backend.schemas.order import ParsedOrderData, MessageClassification, DetectedLanguage
from backend.prompts.order_parser import (
    build_system_prompt,
    build_user_message,
    build_unit_roster,
    build_grid_info,
    build_optimized_local_prompt,
)
from backend.services.retrieval_context import build_order_parser_context
from backend.services.local_triage import local_triage

logger = logging.getLogger(__name__)

# Max retries on LLM parse failure (all models get MAX_RETRIES + 1 attempts)
MAX_RETRIES = 2

# Confidence thresholds for model routing (commands ALWAYS go to LLM)
CONF_SKIP_LLM = 0.95    # only non-commands (acks, reports) can skip LLM
CONF_USE_NANO = 0.50     # partial match, use cheap model
# Below CONF_USE_NANO → use full model


# ── JSON fixups for local model output ────────────────────────
# Small models often return strings instead of lists, dicts instead of lists, etc.
_LIST_FIELDS = {
    "status_request_focus", "target_unit_refs", "location_refs",
    "coordination_unit_refs", "ambiguities",
}
_NULLABLE_STR_FIELDS = {
    "sender_ref", "order_type", "speed", "formation", "engagement_rules",
    "urgency", "purpose", "support_target_ref", "coordination_kind",
    "maneuver_kind", "maneuver_side", "map_object_type", "merge_target_ref",
    "split_ratio", "report_text",
}


def _fixup_llm_json(d: dict) -> None:
    """Coerce common local-model type mistakes in-place."""
    for k in _LIST_FIELDS:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            d[k] = [v] if v else []
        elif isinstance(v, dict):
            d[k] = list(v.values()) if v else []
    for k in _NULLABLE_STR_FIELDS:
        v = d.get(k)
        if isinstance(v, list):
            d[k] = v[0] if v else None
        elif isinstance(v, dict):
            d[k] = None
    # Drop location_refs with invalid ref_type
    _VALID_REF_TYPES = {"snail", "grid", "coordinate", "relative", "height", "terrain"}
    locs = d.get("location_refs")
    if isinstance(locs, list):
        d["location_refs"] = [
            lr for lr in locs
            if isinstance(lr, dict) and lr.get("ref_type") in _VALID_REF_TYPES
        ]


def _is_timeout_error(err: Exception | str) -> bool:
    """Best-effort timeout detection across OpenAI/httpx/client wrappers."""
    text = str(err).lower()
    timeout_markers = (
        "request timed out",
        "timed out",
        "timeout",
        "read timeout",
        "connect timeout",
    )
    return any(marker in text for marker in timeout_markers)


def _repair_json(text: str) -> str:
    """
    Attempt to repair truncated / malformed JSON from local models.

    Handles:
    - Truncated output (unmatched braces/brackets)
    - Trailing commas before } or ]
    - Unquoted string values cut mid-token
    """
    # Find the first '{' — skip any preamble text
    start = text.find("{")
    if start == -1:
        return text
    s = text[start:]

    # Try parsing as-is first
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # Remove trailing incomplete key-value pairs after last complete value
    # Strategy: close all open brackets/braces
    # First, strip any trailing incomplete string (unmatched quote)
    in_string = False
    escape = False
    last_good = 0
    depth_brace = 0
    depth_bracket = 0
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            if not in_string:
                last_good = i
            continue
        if in_string:
            continue
        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
            last_good = i
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1
            last_good = i
        elif ch in (',', ':'):
            last_good = i

    # Truncate at last structurally valid position
    if depth_brace > 0 or depth_bracket > 0 or in_string:
        # Cut back to last complete value
        s = s[:last_good + 1]
        # Remove trailing comma
        s = s.rstrip().rstrip(',')
        # Close remaining open structures
        # Recount
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape = False
        for ch in s:
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth_brace += 1
            elif ch == '}':
                depth_brace -= 1
            elif ch == '[':
                depth_bracket += 1
            elif ch == ']':
                depth_bracket -= 1
        s += ']' * depth_bracket + '}' * depth_brace

    # Remove trailing commas before } or ]
    s = re.sub(r',\s*([}\]])', r'\1', s)

    return s


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str
    is_local: bool
    retrieved: object
    cache_key: str


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
        self._prompt_result_cache: OrderedDict[str, tuple[float, ParsedOrderData]] = OrderedDict()
        self._prompt_result_cache_ttl_s = 300.0
        self._prompt_result_cache_max = 256

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=15.0,  # 15s total timeout per request
            )
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

    def _make_prompt_cache_key(self, *, model: str, system: str, user: str) -> str:
        digest = sha256()
        digest.update(model.encode("utf-8"))
        digest.update(b"\n<system>\n")
        digest.update(system.encode("utf-8"))
        digest.update(b"\n<user>\n")
        digest.update(user.encode("utf-8"))
        return digest.hexdigest()

    def _get_cached_prompt_result(self, cache_key: str) -> ParsedOrderData | None:
        cached = self._prompt_result_cache.get(cache_key)
        if not cached:
            return None
        timestamp, parsed = cached
        if (time.monotonic() - timestamp) > self._prompt_result_cache_ttl_s:
            self._prompt_result_cache.pop(cache_key, None)
            return None
        self._prompt_result_cache.move_to_end(cache_key)
        return parsed.model_copy(deep=True)

    def _store_cached_prompt_result(self, cache_key: str, parsed: ParsedOrderData) -> None:
        self._prompt_result_cache[cache_key] = (time.monotonic(), parsed.model_copy(deep=True))
        self._prompt_result_cache.move_to_end(cache_key)
        while len(self._prompt_result_cache) > self._prompt_result_cache_max:
            self._prompt_result_cache.popitem(last=False)

    def _build_prompt_bundle(
        self,
        *,
        original_text: str,
        units: list[dict],
        grid_info: dict | None,
        game_time: str,
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
        keyword_hint: ParsedOrderData | None = None,
    ) -> PromptBundle:
        """Build the exact prompt pair used for an LLM call, plus cache key."""
        if issuer_side:
            filtered_units = [
                u for u in units
                if u.get("side") == issuer_side and not u.get("is_destroyed")
            ]
        else:
            filtered_units = [u for u in units if not u.get("is_destroyed")]

        if keyword_hint is None:
            keyword_hint = self._fallback_parse(original_text)

        doctrine_topics = self._infer_doctrine_topics(original_text)
        is_local = model == settings.LOCAL_MODEL_NAME
        context_profile = "local" if is_local else "cloud"

        retrieved = build_order_parser_context(
            original_text=original_text,
            parsed_hint=keyword_hint,
            doctrine_topics=doctrine_topics,
            units=filtered_units,
            grid_info=grid_info,
            terrain_context=terrain_context or "",
            contacts_context=contacts_context or "",
            objectives_context=objectives_context or "",
            friendly_status_context=friendly_status_context or "",
            environment_context=environment_context or "",
            orders_context=orders_context or "",
            radio_context=radio_context or "",
            reports_context=reports_context or "",
            map_objects_context=map_objects_context or "",
            profile=context_profile,
        )

        if is_local:
            system, user_context = build_optimized_local_prompt(
                units=retrieved.units_for_prompt,
                order_type_hint=keyword_hint.order_type.value if keyword_hint.order_type else None,
                language_hint=keyword_hint.language.value if keyword_hint.language else None,
                grid_info=grid_info,
                doctrine_excerpt=retrieved.doctrine_text,
                state_packet=retrieved.state_packet,
                continuity_hints=retrieved.continuity_hints,
                contacts_summary=retrieved.contacts_context,
                objectives_summary=retrieved.objectives_context,
                terrain_summary=retrieved.terrain_context,
                history_summary=retrieved.history_digest,
                map_objects_summary=retrieved.map_objects_context,
                environment_summary=retrieved.environment_context,
                friendly_status_summary=retrieved.friendly_status_context,
                height_tops_context=retrieved.height_tops_context,
                game_time=game_time or "",
            )
            user_msg = build_user_message(
                original_text,
                order_type_hint=keyword_hint.order_type.value if keyword_hint.order_type else None,
                language_hint=keyword_hint.language.value if keyword_hint.language else None,
                context_block=user_context,
                include_examples=False,
            )
        else:
            system = build_system_prompt(
                retrieved.doctrine_text
            ).format(
                unit_roster=build_unit_roster(retrieved.units_for_prompt),
                grid_info=build_grid_info(grid_info),
                game_time=game_time or "Unknown",
                height_tops_context=retrieved.height_tops_context or "No relevant height-top context retrieved.",
                terrain_context=retrieved.terrain_context,
                contacts_context=retrieved.contacts_context,
                objectives_context=retrieved.objectives_context,
                friendly_status_context=retrieved.friendly_status_context,
                environment_context=retrieved.environment_context,
                orders_context=retrieved.orders_context,
                radio_context=retrieved.radio_context,
                reports_context=retrieved.reports_context,
                map_objects_context=retrieved.map_objects_context,
            )
            user_msg = build_user_message(
                original_text,
                order_type_hint=keyword_hint.order_type.value if keyword_hint.order_type else None,
                language_hint=keyword_hint.language.value if keyword_hint.language else None,
                context_block="\n".join(
                    part for part in [retrieved.state_packet, retrieved.continuity_hints] if part
                ),
                max_examples=4,
            )

        cache_key = self._make_prompt_cache_key(model=model, system=system, user=user_msg)
        return PromptBundle(
            system=system,
            user=user_msg,
            is_local=is_local,
            retrieved=retrieved,
            cache_key=cache_key,
        )

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
                    keyword_hint=keyword_result,
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
                    keyword_hint=keyword_result,
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
                logger.info("OrderParser: no API key, using local model as full parser at %s",
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
                    keyword_hint=keyword_result,
                )
                result = self._reconcile_llm_result(original_text, result, keyword_result)
                return result if result else keyword_result
            else:
                logger.warning("OrderParser: no API key and no local model — using keyword result")
                return keyword_result

        # ── Step 5: LLM-first mode (default) — cloud primary with optional local triage ──
        if parsing_mode == "llm_first":
            kw_conf = keyword_result.confidence
            kw_class = keyword_result.classification

            # ── 5a: Run local triage to get a cheap classification hint ──
            triage_result = await local_triage.classify(original_text)
            if triage_result is not None:
                # Merge triage into classification decision:
                # If keyword and triage AGREE on non-command → higher confidence to skip LLM
                # If they DISAGREE → lower confidence to force LLM
                triage_class = triage_result.classification
                _is_non_command_kw = kw_class in (
                    MessageClassification.acknowledgment,
                    MessageClassification.status_report,
                    MessageClassification.status_request,
                )
                _is_non_command_triage = triage_class in (
                    MessageClassification.acknowledgment,
                    MessageClassification.status_report,
                    MessageClassification.status_request,
                )

                if _is_non_command_kw and _is_non_command_triage and kw_class == triage_class:
                    # Both agree it's non-command → boost confidence
                    kw_conf = min(kw_conf + 0.10, 0.98)
                    logger.info(
                        "OrderParser[triage]: keyword+local agree on %s → boosted conf=%.2f",
                        kw_class.value, kw_conf,
                    )
                elif kw_class == MessageClassification.command and triage_class != MessageClassification.command:
                    # Keyword says command, triage disagrees → reduce confidence to force full model
                    kw_conf = min(kw_conf, 0.45)
                    logger.info(
                        "OrderParser[triage]: keyword=command but local=%s → reduced conf=%.2f",
                        triage_class.value, kw_conf,
                    )
                elif kw_class != MessageClassification.command and triage_class == MessageClassification.command:
                    # Triage says command, keyword missed it → override to command, force LLM
                    kw_class = MessageClassification.command
                    kw_conf = 0.50
                    keyword_result = keyword_result.model_copy(
                        update={"classification": MessageClassification.command, "confidence": 0.50}
                    )
                    logger.info(
                        "OrderParser[triage]: local overrides to command (keyword was %s)",
                        keyword_result.classification.value,
                    )

                # Use triage language if keyword didn't detect cyrillic well
                if triage_result.language != keyword_result.language:
                    keyword_result = keyword_result.model_copy(
                        update={"language": triage_result.language}
                    )

            # ── 5b: Skip LLM for clear non-command classifications ──
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

            # ── 5c: Choose cloud model tier ──
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
                keyword_hint=keyword_result,
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
                    keyword_hint=keyword_result,
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
            keyword_hint=keyword_result,
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
                keyword_hint=keyword_result,
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
        keyword_hint: ParsedOrderData | None = None,
    ) -> ParsedOrderData | None:
        """Call LLM and return parsed result, or None on failure."""
        if keyword_hint is None:
            keyword_hint = self._fallback_parse(original_text)

        prompt_bundle = self._build_prompt_bundle(
            original_text=original_text,
            units=units,
            grid_info=grid_info,
            game_time=game_time,
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
            keyword_hint=keyword_hint,
        )
        system = prompt_bundle.system
        user_msg = prompt_bundle.user
        _is_local = prompt_bundle.is_local

        cached_result = self._get_cached_prompt_result(prompt_bundle.cache_key)
        if cached_result is not None:
            logger.info(
                "OrderParser[%s]: prompt-result cache hit (system=%d chars, user=%d chars)",
                model,
                len(system),
                len(user_msg),
            )
            return cached_result

        last_error = None
        raw_content = None

        _max_attempts = MAX_RETRIES + 1
        _exclude_params: set[str] = set()  # params to skip on retry

        # Reasoning models (o1, o3, gpt-*-nano) use internal reasoning tokens
        # that count against max_completion_tokens. They also don't support
        # temperature or response_format in most cases.
        _model_lower = model.lower()
        _is_reasoning = any(tag in _model_lower for tag in ("nano", "o1", "o3", "o4"))

        for attempt in range(_max_attempts):
            try:
                create_kwargs = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_completion_tokens=16384 if _is_reasoning else (1024 if _is_local else 2048),
                )
                # Local models: skip response_format (not always supported)
                if _is_local:
                    create_kwargs.pop("response_format", None)
                # Reasoning models don't support temperature
                if _is_reasoning:
                    del create_kwargs["temperature"]
                # Drop params that failed on previous attempts
                for p in _exclude_params:
                    create_kwargs.pop(p, None)
                # Progressively strip unsupported params (some models reject
                # max_tokens, temperature, or both — e.g. gpt-5-nano).
                response = None
                for _param_attempt in range(3):
                    try:
                        response = await client.chat.completions.create(**create_kwargs)
                        break
                    except Exception as api_err:
                        err_str = str(api_err).lower()
                        stripped = False
                        if ("max_tokens" in err_str or "max_completion_tokens" in err_str):
                            create_kwargs.pop("max_tokens", None)
                            create_kwargs.pop("max_completion_tokens", None)
                            stripped = True
                        if "temperature" in err_str:
                            create_kwargs.pop("temperature", None)
                            stripped = True
                        if "response_format" in err_str or "json" in err_str:
                            create_kwargs.pop("response_format", None)
                            stripped = True
                        if not stripped:
                            raise
                if response is None:
                    raise RuntimeError(f"Failed to call {model} after stripping params")

                choice = response.choices[0]
                raw_content = choice.message.content

                # Log finish_reason for diagnostics
                if choice.finish_reason and choice.finish_reason != "stop":
                    logger.warning(
                        "OrderParser[%s]: finish_reason=%s (attempt %d)",
                        model, choice.finish_reason, attempt + 1,
                    )

                # Some models put output in 'refusal' when they refuse
                if not raw_content and hasattr(choice.message, 'refusal') and choice.message.refusal:
                    raise ValueError(f"LLM refused: {choice.message.refusal}")

                if not raw_content:
                    # finish_reason=length means token budget exhausted (reasoning ate all tokens)
                    _reason = choice.finish_reason
                    raise ValueError(
                        f"Empty LLM response (finish_reason={_reason}, "
                        f"usage={getattr(response, 'usage', None)})"
                    )

                # Strip markdown code fences if present (```json ... ```)
                _content = raw_content.strip()
                if _content.startswith("```"):
                    first_nl = _content.find("\n")
                    if first_nl != -1:
                        _content = _content[first_nl + 1:]
                    if _content.endswith("```"):
                        _content = _content[:-3].strip()

                raw_json = json.loads(_repair_json(_content))
                # ── Fix common local-model type mistakes ──
                _fixup_llm_json(raw_json)
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
                self._store_cached_prompt_result(prompt_bundle.cache_key, parsed)
                return parsed

            except json.JSONDecodeError as e:
                last_error = f"JSON decode error: {e}"
                logger.warning("OrderParser[%s] attempt %d: %s. Raw: %s",
                               model, attempt + 1, last_error,
                               raw_content[:300] if raw_content else "empty")
                _exclude_params.add("response_format")
            except ValidationError as e:
                last_error = f"Pydantic validation: {e}"
                logger.warning("OrderParser[%s] attempt %d: %s. Raw JSON keys: %s",
                               model, attempt + 1, last_error,
                               list(raw_json.keys()) if 'raw_json' in dir() else "N/A")
            except Exception as e:
                last_error = str(e)
                logger.warning("OrderParser[%s] attempt %d: %s", model, attempt + 1, last_error)
                # For cheap nano-tier parsing, repeated timeouts are worse than
                # falling back to the deterministic keyword parser immediately.
                if _is_timeout_error(e) and model == settings.OPENAI_MODEL_NANO:
                    logger.warning(
                        "OrderParser[%s]: timeout on attempt %d — bailing out to keyword fallback",
                        model,
                        attempt + 1,
                    )
                    break
                if "Empty LLM response" in last_error:
                    # finish_reason=length → reasoning model ran out of token budget
                    # Remove cap so next attempt gets unlimited tokens
                    _exclude_params.update({"max_completion_tokens", "max_tokens"})
                    if "finish_reason=length" in last_error:
                        _exclude_params.add("response_format")  # also try without json mode

        logger.error("OrderParser[%s]: failed after %d attempts (%s)",
                      model, _max_attempts, last_error)
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
                         "resupply", "re-supply", "rearm", "reload", "replenish",
                         "put smoke", "lay smoke", "deploy smoke", "smoke the", "set smoke",
                         "airlift", "insert", "extract", "landing zone", "lz",
                         "screen", "recon"]
        command_kw_ru = ["выдвигай", "двигай", "движен", "марш", "атак", "оборон", "удержи", "наблюда", "отход",
                         "отступ", "поддерж", "стой", "стоп", "обход", "огонь по", "огонь на",
                         "открыть огонь", "стреляй", "разорвать контакт", "разорви контакт",
                         "выйти из боя", "выйди из боя", "отцепи", "перестрои", "построение",
                         "поразить", "бей по", "удар по", "подавить", "подавляющ",
                         "захвати", "захват", "овладе", "занять", "займ",
                         "продолжай", "будьте готовы", "координируй", "организуй", "прикрой",
                         "откройте огонь", "открывай", "выдвину", "приказываю",
                         "наведите", "наведи", "наводите", "наводи",
                         "вызовите огонь", "вызови огонь", "запросите огонь", "запроси огонь",
                         "артиллерию на", "миномёт на", "минометн на",
                         "раздел", "выдел", "отдели", "слей", "объедин", "соединись",
                         "проделай проход", "проделайте проход", "разминир", "разминируй",
                         "сними заграждение", "минируй", "заминируй", "ставь мины",
                         "установи мины", "навести мост", "разверни мост", "оборудуй",
                         "окопай", "укрепи", "построй", "возведи", "подвези",
                         "поставь дым", "поставьте дым", "накрой дымом", "прикрой дымом",
                          "снабди", "эвакуируй", "десант", "высад", "погруз", "выгруз",
                         "свяжись", "свяжитесь", "следуй за", "следовать за"]
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
        elif any(kw in text_lower for kw in status_req_kw) and not has_command_kw:
            classification = MessageClassification.status_request
            order_type = "report_status"
            status_request_focus = _infer_status_request_focus(text)
        elif has_status_request_frame and not has_command_kw and any(
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
        _has_enemy_reference = False
        _has_flank_maneuver = False
        _has_follow_maneuver = False
        _has_bounding_maneuver = False
        _has_support_by_fire = False
        if classification == MessageClassification.command:
            # ── Standby / ready-for-support detection ──
            # "Get ready for fire support on request" / "Будьте готовы к огневой поддержке по запросу"
            # should NOT be an immediate fire order — unit should stand by (observe)
            _standby_kw = [
                "get ready", "stand by", "standby", "be ready", "on request",
                "on call", "when called", "when requested", "prepare to support",
                "ready to support", "prepare for support",
                "готовность", "готовьтесь", "будьте готовы", "будь готов",
                "по запросу", "по вызову", "по команде", "ожидайте", "ждите",
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
                "request smoke", "call smoke", "request mortar smoke",
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
                "запросите дым", "запроси дым",
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
            # even if "артиллерию/миномёт" is not right после
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
                "screen the left flank", "screen the right flank",
                "screen forward", "screen this axis",
                "прикрой фланг наблюдением", "прикрыть фланг наблюдением",
                "прикрой левый фланг", "прикрой правый фланг",
                "прикрой левый фланг наблюдением", "прикрой правый фланг наблюдением",
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
            elif _has_screen_mission:
                order_type = "observe"
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
            elif _has_screen_mission or any(kw in text_lower for kw in ["observe", "наблюда", "наблюден", "recon", "развед"]):
                order_type = "observe"
            elif (
                re.search(r"\bmove\b", text_lower) is not None
                or any(kw in text_lower for kw in ["advance", "form ", "выдвигай", "двигай",
                                                   "движен", "марш", "обход", "перестрои", "построение"])
            ):
                order_type = "move"
                if _has_follow_maneuver:
                    maneuver_kind = "follow"
                elif _has_bounding_maneuver:
                    maneuver_kind = "bounding"
            elif any(kw in text_lower for kw in ["defend", "hold", "оборон", "удержи"]):
                order_type = "defend"
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
            elif _has_follow_maneuver:
                order_type = "move"
                maneuver_kind = "follow"

        # Extract snail/grid references with regex
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
            ("minefield", ["minefield", "mines", "минное поле", "минном поле", "минного поля", "минному полю", "мины"]),
            ("barbed_wire", ["barbed wire", "wire obstacle", "колючая проволока", "проволочное заграждение"]),
            ("concertina_wire", ["concertina", "razor wire", "спираль бруно", "егоза"]),
            ("roadblock", ["roadblock", "checkpoint", "блокпост", "дорожное заграждение"]),
            ("anti_tank_ditch", ["anti-tank ditch", "tank ditch", "противотанковый ров"]),
            ("dragons_teeth", ["dragon's teeth", "dragons teeth", "надолбы", "зубы дракона"]),
            ("entrenchment", ["entrenchment", "trench", "foxhole", "окоп", "траншея", "укреплен"]),
            ("observation_tower", ["observation tower", "watchtower", "вышка", "наблюдательный пост"]),
            ("field_hospital", ["field hospital", "aid station", "медпункт", "полевой госпиталь"]),
            ("smoke", ["smoke", "smokescreen", "дым", "дымовая завеса"]),
            ("command_post_structure", ["command post", "hq post", "командный пункт", "кп"]),
            ("supply_cache", ["supply cache", "ammo dump", "supply point", "склад", "пункт снабжения"]),
            ("bridge_structure", ["bridge", "crossing", "мост", "переправа"]),
            ("pillbox", ["pillbox", "bunker", "дот", "дзот"]),
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
                                  "готовность", "готовьтесь", "будьте готовы", "будь готов", "по запросу",
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
                r'поддержать\s+огнём\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)?)',
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
                    raw_ref = match.group(1).strip().rstrip(".,;!")
                    # Truncate at conjunctions: "C-squad and continue..." → "C-squad"
                    raw_ref = _re_merge.split(
                        r'\b(?:and|и|then|чтобы)\b', raw_ref, maxsplit=1, flags=_re_merge.IGNORECASE
                    )[0].strip().rstrip(".,;!")
                    merge_target_ref = raw_ref
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

            # Do not confuse the addressed unit with the coordination partner.
            addressed_refs = {ref.lower() for ref in target_unit_refs}
            coordination_unit_refs = [
                ref for ref in coordination_unit_refs
                if ref.lower() not in addressed_refs
            ]

            # If the text refers to "the mortars"/"artillery" generically, ensure
            # a canonical ref survives the addressed-refs filter above.
            if any(kw in text_lower for kw in ["миномёт", "миномет", "mortar"]):
                if not any("мином" in ref.lower() or "mortar" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Mortar")
            elif any(kw in text_lower for kw in ["артиллер", "artillery"]):
                if not any("артилл" in ref.lower() or "artillery" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Artillery")


            if any(kw in text_lower for kw in [
                "прикры", "covering fire", "cover me", "cover your movement",
                "covers your", "cover your", "covers my",
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



