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
from backend.schemas.order import ParsedOrderData, MessageClassification, DetectedLanguage, OrderType
from backend.prompts.order_parser import (
    build_system_prompt,
    build_user_message,
    build_unit_roster,
    build_grid_info,
    build_optimized_local_prompt,
)
from backend.services.order_phrasebook import get_order_parser_lexicon
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

    @staticmethod
    def _can_fast_path_keyword_parse(parsed: ParsedOrderData) -> bool:
        """
        Allow a narrow, deterministic fast-path for trivial movement orders.

        These are already parsed correctly by the keyword parser and do not
        benefit much from a cloud round-trip, so skipping LLM avoids tens of
        seconds of latency on obvious move commands.
        """
        if parsed.classification != MessageClassification.command:
            return False
        if parsed.confidence < 0.80:
            return False
        if parsed.order_type != OrderType.move:
            return False
        if not parsed.location_refs:
            return False
        if parsed.coordination_unit_refs:
            return False
        if parsed.merge_target_ref or parsed.split_ratio is not None:
            return False
        if parsed.map_object_type:
            return False
        return True

    @staticmethod
    def _should_run_local_triage(keyword_result: ParsedOrderData) -> bool:
        """
        Local triage is useful when classification is uncertain, but it adds
        avoidable latency on already-obvious commands.
        """
        return not (
            keyword_result.classification == MessageClassification.command
            and keyword_result.confidence >= 0.75
        )

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

        if self._can_fast_path_keyword_parse(keyword_result):
            logger.info(
                "OrderParser: strong keyword parse for %s (conf=%.2f) — skipping LLM",
                keyword_result.order_type.value if keyword_result.order_type else "unknown",
                keyword_result.confidence,
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
            _is_compound = False

            # ── 5a: Run local triage to get a cheap classification hint ──
            triage_result = None
            if self._should_run_local_triage(keyword_result):
                triage_result = await local_triage.classify(original_text)
            else:
                logger.info(
                    "OrderParser[triage]: skipping local triage for confident command "
                    "class=%s conf=%.2f",
                    kw_class.value,
                    kw_conf,
                )
                # Even when skipping full triage, check for compound commands via keywords
                from backend.services.local_triage import detect_compound_keyword
                if detect_compound_keyword(original_text):
                    _is_compound = True
                    kw_conf = min(kw_conf, 0.40)
                    logger.info(
                        "OrderParser[triage]: compound command detected via keywords → conf=%.2f",
                        kw_conf,
                    )

            if triage_result is not None:
                # ── Compound command detection ──
                if triage_result.is_compound:
                    _is_compound = True
                    kw_class = MessageClassification.command
                    kw_conf = min(kw_conf, 0.35)  # forces full model
                    keyword_result = keyword_result.model_copy(
                        update={
                            "classification": MessageClassification.command,
                            "confidence": kw_conf,
                            "ambiguities": [
                                *(keyword_result.ambiguities or []),
                                "Compound/multi-step command detected — requires full model parsing",
                            ],
                        }
                    )
                    logger.info(
                        "OrderParser[triage]: compound command → forced full model (conf=%.2f)",
                        kw_conf,
                    )

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

        lexicon = get_order_parser_lexicon()
        classification_lexicon = lexicon["classification"]
        question_lexicon = lexicon["question_markers"]
        order_detection_lexicon = lexicon["order_detection"]
        speed_lexicon = lexicon["speed"]
        formation_lexicon = lexicon["formation"]
        engagement_lexicon = lexicon["engagement"]
        location_lexicon = lexicon["location"]

        # Classification keywords
        command_kw_en = classification_lexicon["command_en"]
        command_kw_ru = classification_lexicon["command_ru"]
        status_req_kw = classification_lexicon["status_request_keywords"]
        status_req_focus_map = lexicon["status_request_focus"]
        ack_kw = classification_lexicon["acknowledgment"]
        report_kw = classification_lexicon["report"]

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

        def _has_any(phrases: list[str]) -> bool:
            return any(phrase in text_lower for phrase in phrases)

        has_status_request_frame = self._has_status_request_frame(text)

        # ── Question detection ──────────────────────────────
        # Questions like "Почему не наводите?" / "Why aren't you..." are NOT commands.
        # They're either status requests or unclear messages that need LLM escalation.
        _question_words_ru = question_lexicon["ru"]
        _question_words_en = question_lexicon["en"]
        def _has_question_marker(raw_text: str, marker: str) -> bool:
            return re.search(
                rf"(^|\s){re.escape(marker)}(?=\s|\?|$)",
                raw_text,
                re.IGNORECASE,
            ) is not None

        _is_question = (
            any(_has_question_marker(text_lower, q) for q in _question_words_ru + _question_words_en)
            or (text.strip().endswith("?") and any(
                _has_question_marker(text_lower, q) for q in _question_words_ru + _question_words_en
            ))
        )

        # Strong sender-identification signals → status report or ack
        is_self_report = any(kw in text_lower for kw in classification_lexicon["self_report_markers"])

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
        elif any(kw in text_lower for kw in classification_lexicon["command_inference"]):
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
            _standby_kw = order_detection_lexicon["standby"]
            _is_standby = _has_any(_standby_kw)

            # ── Coordination detection ──
            # "Lead the attack", "coordinate artillery support" — these are attack orders
            # with coordination intent, NOT fire orders for the receiving unit.
            _coordination_kw = order_detection_lexicon["coordination"]
            _is_coordination = _has_any(_coordination_kw)
            
            # ── Request fire detection ──
            # "Request fire support", "Call for fire", "Direct artillery at enemy"
            # Unit should create a fire request to CoC artillery, not attack themselves
            _request_fire_kw = order_detection_lexicon["request_fire"]
            _is_request_fire = _has_any(_request_fire_kw)
            _liaison_only = (
                _has_any(order_detection_lexicon["liaison_only"])
                and not _has_any(order_detection_lexicon["liaison_fire_exclusions"])
            )
            if _liaison_only:
                _is_request_fire = False
            # Also detect "наведите ... на цель" / "наводите ... на цель" patterns
            # even if "артиллерию/миномёт" is not right после
            if not _is_request_fire:
                _navedi_pattern = _has_any(order_detection_lexicon["navedi_verbs"])
                _on_target = _has_any(order_detection_lexicon["navedi_targets"])
                if _navedi_pattern and _on_target:
                    _is_request_fire = True

            _has_enemy_reference = _has_any(order_detection_lexicon["enemy_reference"])
            _has_flank_maneuver = _has_any(order_detection_lexicon["flank_maneuver"])
            _has_follow_maneuver = _has_any(order_detection_lexicon["follow_maneuver"])
            _has_bounding_maneuver = _has_any(order_detection_lexicon["bounding_maneuver"])
            _has_support_by_fire = _has_any(order_detection_lexicon["support_by_fire"])
            _has_delay_mission = _has_any(order_detection_lexicon["delay_mission"])
            _has_halt_order = _has_any(order_detection_lexicon["halt"])
            _has_defend_order = _has_any(order_detection_lexicon["defend"])
            _has_withdraw_order = _has_any(order_detection_lexicon["withdraw"])
            _has_screen_mission = _has_any(order_detection_lexicon["screen"])
            _has_breach_order = _has_any(order_detection_lexicon["breach"])
            _has_lay_mines_order = _has_any(order_detection_lexicon["lay_mines"])
            _has_deploy_bridge_order = _has_any(order_detection_lexicon["deploy_bridge"])
            _has_construct_order = _has_any(order_detection_lexicon["construct"])
            _has_smoke_fire_order = _has_any(order_detection_lexicon["smoke_fire"])
            _has_split_order = _has_any(order_detection_lexicon["split"])
            _has_merge_order = _has_any(order_detection_lexicon["merge"])
            _has_air_mobility_order = _has_any(order_detection_lexicon["air_mobility"])
            # Fire adjustment (artillery correction) and fire direction between units
            _has_fire_adjustment = _has_any(order_detection_lexicon.get("fire_adjustment", []))
            _has_fire_direction = _has_any(order_detection_lexicon.get("fire_direction", []))
            # Regroup / rally
            _has_regroup_order = _has_any(order_detection_lexicon.get("regroup", []))
            # "check fire" / "cease fire" / "прекратить огонь" → halt (cease fire)
            _cease_fire_kw = ["check fire", "cease fire", "прекратить огонь", "прекрати огонь"]
            _has_cease_fire = _has_any(_cease_fire_kw)

            # Determine order type — logic order matters!
            # 1. Standby for support → observe
            # 2. Request fire from CoC artillery → request_fire
            # 3. Coordination orders that mention attack → attack (not fire)
            # 4. Direct fire commands → fire
            # 5. Attack keywords → attack
            # 6. Move keywords → move
            # etc.
            if _is_standby and _has_any(order_detection_lexicon["standby_fire_support"]):
                # Standby for fire support → observe/wait, NOT immediate fire
                order_type = "observe"
            elif _has_cease_fire:
                # "Check fire", "Cease fire", "Прекратить огонь" → halt (stop shooting)
                order_type = "halt"
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
            elif _is_coordination and _has_any(order_detection_lexicon["coordination_attack"]):
                # "Lead the attack", "coordinate the attack" → attack with coordination
                order_type = "attack"
            elif _has_any(order_detection_lexicon["direct_fire"]):
                # Direct fire commands → fire
                order_type = "fire"
            elif _has_fire_adjustment:
                # "Adjust fire", "Корректировка", "Fire for effect" → fire
                order_type = "fire"
            elif _has_fire_direction:
                # "Concentrate fire on", "Сосредоточить огонь" → fire
                order_type = "fire"
            elif _has_any(order_detection_lexicon["suppression"]):
                # Suppression orders → fire
                order_type = "fire"
            elif not _is_coordination and _has_any(order_detection_lexicon["fire_support_general"]):
                # "Request artillery support on grid X" → fire (but not if it's coordination)
                order_type = "fire"
            elif _has_any(order_detection_lexicon["fire_at_will_attack"]):
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
            elif re.search(r"\bdisengage\b", text_lower):
                order_type = "disengage"
            elif _has_halt_order:
                order_type = "halt"
            elif _has_defend_order:
                order_type = "defend"
            # Check attack/eliminate BEFORE move: "Move to X. Eliminate enemy" should be attack
            elif _has_any(order_detection_lexicon["attack"]):
                order_type = "attack"
            elif _has_air_mobility_order:
                order_type = "move"
            elif _has_screen_mission or _has_any(order_detection_lexicon["observe"]):
                order_type = "observe"
            elif (
                re.search(r"\bmove\b", text_lower) is not None
                or _has_any(order_detection_lexicon["move"])
            ):
                order_type = "move"
                if _has_follow_maneuver:
                    maneuver_kind = "follow"
                elif _has_bounding_maneuver:
                    maneuver_kind = "bounding"
            elif _has_any(order_detection_lexicon["simple_defend"]):
                order_type = "defend"
            elif _has_any(order_detection_lexicon["disengage"]):
                order_type = "disengage"
            elif _has_withdraw_order:
                order_type = "withdraw"
            elif _has_any(order_detection_lexicon["halt"]):
                order_type = "halt"
            elif _has_any(order_detection_lexicon["resupply"]):
                order_type = "resupply"
            # ── Aviation orders ──
            elif _has_any(order_detection_lexicon.get("air_assault", [])):
                order_type = "air_assault"
            elif _has_any(order_detection_lexicon.get("casevac", [])):
                order_type = "casevac"
            elif _has_any(order_detection_lexicon.get("airstrike", [])):
                order_type = "airstrike"
            elif _has_regroup_order:
                order_type = "move"  # regroup = move to rally point
            elif _has_follow_maneuver:
                order_type = "move"
                maneuver_kind = "follow"

            # ── Fallback: command with location but no order type → move ──
            # Colloquial phrases like "давай к F7-5-6" are commands but don't
            # match any specific order detection, yet having a location implies movement.
            if order_type is None and classification == MessageClassification.command:
                _has_loc = bool(location_refs) if 'location_refs' in dir() else False
                if not _has_loc:
                    # Pre-check for locations in text (snail or grid patterns)
                    _has_loc = bool(re.search(r'[A-Za-z]\d+(?:-\d)', text))
                if _has_loc:
                    order_type = "move"

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

        map_object_patterns = location_lexicon["map_object_patterns"]
        for entry in map_object_patterns:
            candidate_type = entry["name"]
            patterns = entry["patterns"]
            if any(pattern in text_lower for pattern in patterns):
                map_object_type = candidate_type
                break

        if order_type == "lay_mines" and map_object_type is None:
            map_object_type = "minefield"

        has_resolved_location = any(
            lr["ref_type"] in {"snail", "grid", "coordinate", "height", "map_object"}
            for lr in location_refs
        )
        implied_contact_target = (
            _has_enemy_reference
            or _has_any(order_detection_lexicon["implied_contact_target"])
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
        _target_phrases_ru = order_detection_lexicon["target_phrases_ru"]
        _target_phrases_en = order_detection_lexicon["target_phrases_en"]
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
                and _has_any(order_detection_lexicon["enemy_reference"])
                and _has_any(order_detection_lexicon["flank_contact_target"])
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
        slow_kw = speed_lexicon["slow"]
        fast_kw = speed_lexicon["fast"]
        if any(kw in text_lower for kw in slow_kw):
            speed = "slow"
        elif any(kw in text_lower for kw in fast_kw):
            speed = "fast"

        # ── Formation detection ─────────────────────────────────
        formation = None
        formation_map = {
            pattern: form_name
            for form_name, patterns in formation_lexicon["patterns"].items()
            for pattern in patterns
        }
        # Check formation patterns (check longer patterns first)
        for pattern, form_name in sorted(formation_map.items(), key=lambda x: -len(x[0])):
            if pattern in text_lower:
                formation = form_name
                break
        if not formation:
            if _has_any(formation_lexicon["maneuver_side_left"]):
                formation = "echelon_left"
                maneuver_side = "left"
            elif _has_any(formation_lexicon["maneuver_side_right"]):
                formation = "echelon_right"
                maneuver_side = "right"
        elif formation == "echelon_left":
            maneuver_side = maneuver_side or "left"
        elif formation == "echelon_right":
            maneuver_side = maneuver_side or "right"

        if (
            formation == "line"
            and order_type in ("withdraw", "disengage")
            and re.search(r"\b(?:to|toward|towards|along)\s+line\s+[a-z]\d", text_lower)
        ):
            formation = None

        # Also look for explicit formation commands
        import re as _re
        _formation_prefixes = "|".join(
            _re.escape(prefix)
            for prefix in sorted(formation_lexicon["explicit_prefixes"], key=len, reverse=True)
        )
        _formation_targets = "|".join(
            _re.escape(pattern)
            for pattern in sorted(formation_map, key=len, reverse=True)
        )
        form_cmd = _re.search(
            rf'(?:{_formation_prefixes})\s*[:=]?\s*({_formation_targets})',
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
        if _has_any(engagement_lexicon["hold_fire"]):
            engagement_rules = "hold_fire"
        elif _has_any(engagement_lexicon["fire_at_will"]):
            engagement_rules = "fire_at_will"
        elif _has_any(engagement_lexicon["return_fire_only"]):
            engagement_rules = "return_fire_only"

        # ── Support target ref: extract the unit being supported in standby orders ──
        # e.g. "be ready to support C-squad's targets" → support_target_ref = "C-squad"
        # e.g. "Будьте готовы поддержать огнём по целям, которые вам передаст C-squad" → "C-squad"
        support_target_ref = None
        merge_target_ref = None
        split_ratio = None
        _is_standby_check = (classification == MessageClassification.command
                             and order_type == "observe"
                             and _has_any(order_detection_lexicon["standby_support_markers"]))
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
                r'объедини(?:сь|тесь)\s+с\s+([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-ZazlА-Яа-яё][\w-]+)*)',
                r'слей(?:ся|тесь)\s+с\s+([A-ZazlА-Яа-яё][\w-]+(?:\s+[A-ZazlА-Яа-яё][\w-]+)*)',
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
                # Fire direction: "наведите артиллерию Art1 на цель" / "direct artillery Art1 at target"
                r'навед(?:и|ите)\s+(?:артиллери\w*|миномёт\w*|минометн\w*|огонь)\s+([A-Za-zА-Яа-яё][\w-]+)',
                r'direct\s+(?:artillery|mortar|fire(?:\s+from)?)\s+([A-Za-z][\w-]+)',
                r'call\s+(?:in\s+)?(?:fire\s+from|artillery\s+from|strikes?\s+from)\s+([A-Za-z][\w-]+)',
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
                r'следуй(?:те)?\s+за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'следовать\s+за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'иди(?:те)?\s+за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'двигай(?:ся|тесь|те)?\s+(?:вслед\s+)?за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'держ(?:ись|итесь|аться)\s+за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
                r'вслед\s+за\s+(?:группой\s+)?([A-Za-zА-Яа-яё][\w-]+(?:\s+[A-Za-zА-Яа-яё][\w-]+)*)',
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
            if _has_any(order_detection_lexicon["generic_mortar"]):
                if not any("мином" in ref.lower() or "mortar" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Mortar")
            elif _has_any(order_detection_lexicon["generic_artillery"]):
                if not any("артилл" in ref.lower() or "artillery" in ref.lower() for ref in coordination_unit_refs):
                    coordination_unit_refs.append("Artillery")


            if _has_any(order_detection_lexicon["coordination_covering_fire"]):
                coordination_kind = "covering_fire"
            elif _has_any(order_detection_lexicon["coordination_fire_support"]):
                coordination_kind = "fire_support"
            elif coordination_unit_refs:
                coordination_kind = "coordination"

        if maneuver_kind == "flank" and maneuver_side is None:
            if _has_any(formation_lexicon["maneuver_side_left"]):
                maneuver_side = "left"
            elif _has_any(formation_lexicon["maneuver_side_right"]):
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
            move_verbs = ["move", "advance", "proceed", "выдвигай", "двигай", "марш", "движен",
                          "rally", "regroup", "перегруппируй"]
            attack_verbs = ["attack", "engage", "eliminate", "destroy", "neutralize",
                           "fire", "shoot", "suppress", "assault", "storm",
                           "атак", "уничтож", "ликвидир", "поразить", "огонь", "подавить",
                           "штурмуй"]
            defend_verbs = ["defend", "hold", "fortify", "entrench", "dig in",
                           "оборон", "удержи", "окопай", "укрепи"]
            observe_verbs = ["observe", "recon", "scout", "screen", "overwatch",
                            "наблюда", "разведк", "прикрой наблюдением"]
            engineer_verbs = ["breach", "mine", "bridge", "construct", "build",
                             "разминир", "минируй", "мост", "построй"]
            # Coordination/leadership verbs indicate complex multi-unit orders
            coord_verbs = ["coordinate", "lead", "organize", "direct", "command",
                          "координируй", "организуй", "возглав", "руковод", "командуй"]

            has_move = any(v in text_lower for v in move_verbs)
            has_attack = any(v in text_lower for v in attack_verbs)
            has_defend = any(v in text_lower for v in defend_verbs)
            has_observe = any(v in text_lower for v in observe_verbs)
            has_coord = any(v in text_lower for v in coord_verbs)
            has_engineer = any(v in text_lower for v in engineer_verbs)

            verb_count = sum([has_move, has_attack, has_defend, has_observe, has_engineer])

            if verb_count >= 2:
                # Multiple action verbs → complex command → reduce confidence
                # This ensures LLM parses the intent correctly
                conf = min(conf, 0.65)  # cap at 0.65 to trigger nano model

            # Coordination orders are inherently complex — always send to LLM
            if has_coord:
                conf = min(conf, 0.50)  # cap at 0.50 to trigger full model

            # Compound/sequential commands → force full model
            from backend.services.local_triage import detect_compound_keyword
            if detect_compound_keyword(original_text):
                conf = min(conf, 0.40)  # cap well below nano threshold

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



