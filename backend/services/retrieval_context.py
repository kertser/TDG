from __future__ import annotations

import re
from dataclasses import dataclass

from backend.prompts.tactical_doctrine import get_tactical_doctrine_excerpt
from backend.schemas.order import ParsedOrderData


@dataclass(frozen=True)
class RetrievedParserContext:
    doctrine_text: str
    doctrine_topics: list[str]
    units_for_prompt: list[dict]
    state_packet: str
    history_digest: str
    continuity_hints: str
    height_tops_context: str
    terrain_context: str
    contacts_context: str
    objectives_context: str
    friendly_status_context: str
    environment_context: str
    orders_context: str
    radio_context: str
    reports_context: str
    map_objects_context: str


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "have",
    "will", "they", "them", "their", "then", "than", "when", "what", "where",
    "which", "over", "near", "same", "continue", "radio", "report", "status",
    "unit", "units", "order", "orders", "при", "для", "как", "что", "это", "или",
    "над", "под", "после", "перед", "если", "буд", "есть", "его", "её", "их",
    "you", "are", "was", "were", "here", "there", "all", "any", "our", "now",
}

_SECTION_FALLBACKS = {
    "terrain": "None.",
    "contacts": "None.",
    "objectives": "None.",
    "friendly_status": "None.",
    "environment": "None.",
    "orders": "None.",
    "radio": "None.",
    "reports": "None.",
    "map_objects": "None.",
}

_BASE_LIMITS = {
    "cloud": {
        "terrain": 6,
        "contacts": 8,
        "objectives": 4,
        "friendly_status": 8,
        "environment": 3,
        "orders": 8,
        "radio": 5,
        "reports": 5,
        "map_objects": 8,
        "units": 18,
        "height_tops": 10,
        "doctrine_passages": 6,
        "doctrine_chars": 2000,
        "state_packet_chars": 2400,
        "history_facts": 8,
        "continuity_facts": 5,
    },
    "local": {
        "terrain": 3,
        "contacts": 4,
        "objectives": 2,
        "friendly_status": 4,
        "environment": 2,
        "orders": 4,
        "radio": 3,
        "reports": 3,
        "map_objects": 4,
        "units": 10,
        "height_tops": 4,
        "doctrine_passages": 2,
        "doctrine_chars": 700,
        "state_packet_chars": 1050,
        "history_facts": 4,
        "continuity_facts": 3,
    },
}

_PROFILE_CONFIG: dict[str, dict] = {
    "move": {
        "topics": ["general", "offense", "recon"],
        "keywords": ["move", "advance", "grid", "snail", "route", "road", "terrain", "contact", "двиг", "марш", "маршрут"],
        "section_keywords": {
            "terrain": ["terrain", "slope", "road", "forest", "urban", "hill", "высот", "дорог", "лес"],
            "orders": ["move", "advance", "attack", "withdraw", "support", "follow", "bound", "move", "выдв", "движ", "обход"],
            "radio": ["continue", "same route", "same target", "continue", "продолж", "как раньше"],
        },
    },
    "attack": {
        "topics": ["general", "offense", "fires", "recon"],
        "keywords": ["attack", "assault", "flank", "enemy", "contact", "support", "атак", "штурм", "охват", "обход"],
        "section_keywords": {
            "contacts": ["enemy", "contact", "observed", "spotted", "противник", "контакт"],
            "reports": ["contact", "enemy", "fire", "spotrep", "противник", "обнаруж"],
            "orders": ["attack", "flank", "support", "fix", "атак", "обход", "охват", "поддерж"],
            "terrain": ["forest", "urban", "ridge", "hill", "лес", "город", "высот"],
        },
    },
    "fire": {
        "topics": ["general", "fires", "recon", "map_objects"],
        "keywords": ["fire", "mortar", "artillery", "target", "smoke", "grid", "огонь", "мином", "артилл", "дым"],
        "section_keywords": {
            "contacts": ["enemy", "contact", "target", "противник", "цель", "контакт"],
            "reports": ["spotrep", "target", "adjust", "smoke", "цель", "коррект", "дым"],
            "map_objects": ["bridge", "crossing", "bunker", "bridge_structure", "smoke", "мост", "переправ", "дым"],
            "radio": ["target", "same target", "fire mission", "цель", "огонь", "мином"],
        },
    },
    "request_fire": {
        "topics": ["general", "fires", "recon"],
        "keywords": ["request", "fire", "artillery", "support", "target", "запрос", "огонь", "артилл", "поддерж"],
        "section_keywords": {
            "contacts": ["enemy", "contact", "target", "противник", "цель"],
            "reports": ["contact", "enemy", "grid", "target", "противник", "цель"],
            "radio": ["fire", "target", "on request", "same target", "огонь", "цель"],
            "orders": ["support", "request_fire", "fire", "поддерж", "запрос"],
        },
    },
    "defend": {
        "topics": ["general", "defense", "engineers", "map_objects"],
        "keywords": ["defend", "hold", "dig", "position", "screen", "оборон", "удерж", "окоп", "позици"],
        "section_keywords": {
            "terrain": ["hill", "ridge", "forest", "urban", "slope", "высот", "склон", "лес"],
            "map_objects": ["entrenchment", "roadblock", "wire", "bunker", "окоп", "заграж", "провол"],
            "friendly_status": ["weak", "ammo", "morale", "offline", "потери", "бк", "мораль"],
            "orders": ["defend", "hold", "observe", "withdraw", "оборон", "удерж", "отход"],
        },
    },
    "observe": {
        "topics": ["general", "recon", "map_objects"],
        "keywords": ["observe", "screen", "report", "watch", "recon", "наблюд", "развед", "долож"],
        "section_keywords": {
            "contacts": ["enemy", "contact", "movement", "противник", "движени", "контакт"],
            "terrain": ["hill", "forest", "cover", "ridge", "высот", "лес", "укрыт"],
            "map_objects": ["bridge", "crossing", "tower", "road", "мост", "переправ", "дорог"],
            "reports": ["spotrep", "movement", "enemy", "противник", "обнаруж"],
        },
    },
    "support": {
        "topics": ["general", "fires", "offense"],
        "keywords": ["support", "covering", "fire", "support by fire", "поддерж", "прикры"],
        "section_keywords": {
            "orders": ["support", "cover", "fire", "поддерж", "прикры"],
            "contacts": ["enemy", "contact", "target", "противник", "цель"],
            "radio": ["support", "target", "coordination", "поддерж", "координац"],
        },
    },
    "breach": {
        "topics": ["general", "engineers", "map_objects", "offense"],
        "keywords": ["breach", "lane", "roadblock", "bridge", "wire", "mine", "breaching", "проход", "размини", "мост", "заграж"],
        "section_keywords": {
            "map_objects": ["roadblock", "bridge", "wire", "minefield", "ditch", "dragon", "road", "roadblock", "мост", "переправ", "мин", "провол", "заграж", "дорог"],
            "terrain": ["road", "water", "bridge", "marsh", "slope", "дорог", "вода", "мост", "болот"],
            "orders": ["breach", "engineer", "support", "smoke", "проход", "сап", "дым"],
            "contacts": ["enemy", "contact", "fire", "противник", "контакт"],
        },
    },
    "lay_mines": {
        "topics": ["general", "engineers", "map_objects", "defense"],
        "keywords": ["mine", "emplace", "lane", "approach", "заминир", "мины", "подступ"],
        "section_keywords": {
            "terrain": ["road", "approach", "bridge", "open", "дорог", "подступ", "переправ"],
            "map_objects": ["minefield", "roadblock", "wire", "bridge", "мин", "провол", "мост"],
            "orders": ["lay_mines", "defend", "delay", "мини", "оборон", "задерж"],
        },
    },
    "construct": {
        "topics": ["general", "engineers", "logistics", "map_objects", "defense"],
        "keywords": ["construct", "build", "fortify", "command post", "hospital", "cache", "построй", "укреп", "командн", "госпитал"],
        "section_keywords": {
            "map_objects": ["command_post", "hospital", "cache", "entrenchment", "command post", "field hospital", "supply", "команд", "госпитал", "склад", "укреп"],
            "terrain": ["hill", "forest", "urban", "road", "высот", "лес", "дорог"],
            "friendly_status": ["ammo", "weak", "morale", "бк", "потери"],
        },
    },
    "deploy_bridge": {
        "topics": ["general", "engineers", "map_objects", "aviation"],
        "keywords": ["bridge", "crossing", "river", "deploy bridge", "мост", "переправ", "река"],
        "section_keywords": {
            "map_objects": ["bridge", "crossing", "water", "river", "мост", "переправ", "вода", "река"],
            "terrain": ["water", "marsh", "road", "bridge", "вода", "болот", "дорог", "мост"],
            "orders": ["bridge", "breach", "move", "мост", "переправ", "проход"],
        },
    },
    "split": {
        "topics": ["general", "split_merge", "recon", "offense"],
        "keywords": ["split", "detach", "screen", "one third", "раздел", "выдел", "отдел"],
        "section_keywords": {
            "orders": ["split", "detach", "screen", "merge", "раздел", "выдел", "объедин"],
            "friendly_status": ["strength", "ammo", "morale", "task", "сила", "бк", "задач"],
            "radio": ["screen", "support", "follow", "наблюд", "прикры", "следуй"],
        },
    },
    "merge": {
        "topics": ["general", "split_merge", "logistics"],
        "keywords": ["merge", "join", "combine", "one element", "слей", "объедин", "соедини"],
        "section_keywords": {
            "orders": ["merge", "split", "regroup", "combine", "объедин", "слей", "перегруп"],
            "friendly_status": ["strength", "ammo", "morale", "position", "сила", "бк", "мораль", "позици"],
            "radio": ["join", "merge", "rally", "соедини", "сбор"],
        },
    },
    "resupply": {
        "topics": ["general", "logistics", "defense"],
        "keywords": ["resupply", "ammo", "supply", "cache", "rearm", "снабд", "боеприпас", "бк"],
        "section_keywords": {
            "friendly_status": ["ammo", "weak", "morale", "ready", "бк", "боеприпас", "потери"],
            "map_objects": ["supply", "cache", "command post", "склад", "снабж"],
            "orders": ["resupply", "move", "supply", "снабд", "подвез"],
        },
    },
    "withdraw": {
        "topics": ["general", "defense", "recon"],
        "keywords": ["withdraw", "retreat", "fallback", "отход", "отступ"],
        "section_keywords": {
            "terrain": ["road", "cover", "forest", "ridge", "дорог", "укрыт", "лес"],
            "contacts": ["enemy", "contact", "pressure", "противник", "контакт"],
            "orders": ["withdraw", "delay", "defend", "отход", "задерж"],
        },
    },
    "disengage": {
        "topics": ["general", "defense", "fires"],
        "keywords": ["disengage", "break contact", "cover", "разорвать контакт", "выйти из боя"],
        "section_keywords": {
            "contacts": ["enemy", "contact", "close", "противник", "контакт"],
            "terrain": ["cover", "forest", "urban", "road", "укрыт", "лес", "город"],
            "orders": ["disengage", "withdraw", "support", "разорвать", "отход"],
        },
    },
}


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9_/-]+", (text or "").lower())
    return [tok for tok in tokens if len(tok) >= 2 and tok not in _STOP_WORDS]


def _clean_bullet(line: str) -> str:
    stripped = (line or "").strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    if stripped.startswith("• "):
        return stripped[2:].strip()
    return stripped


def _split_context(context: str) -> tuple[str | None, list[str]]:
    lines = [line.rstrip() for line in (context or "").splitlines() if line.strip()]
    if not lines:
        return None, []
    header = None
    items = lines
    if not lines[0].lstrip().startswith("-"):
        header = lines[0].strip()
        items = lines[1:]
    bullets = [_clean_bullet(line) for line in items if _clean_bullet(line)]
    return header, bullets


def _score_line(
    line: str,
    query_tokens: set[str],
    section_keywords: list[str],
    line_index: int,
    total_lines: int,
    *,
    prefer_recent: bool = False,
) -> float:
    lowered = line.lower()
    overlap = sum(1 for token in query_tokens if token in lowered)
    section_overlap = sum(1 for token in section_keywords if token in lowered)
    score = overlap * 3.0 + section_overlap * 2.0

    if re.search(r"\b[A-Z]\d(?:-\d+){0,3}\b", line):
        score += 1.0
    if any(ch.isdigit() for ch in line):
        score += 0.25
    if prefer_recent and total_lines > 0:
        score += (line_index / total_lines) * 0.75
    elif total_lines > 0:
        score += ((total_lines - line_index) / total_lines) * 0.35
    return score


def _truncate(text: str, max_chars: int) -> str:
    stripped = (text or "").strip()
    if len(stripped) <= max_chars:
        return stripped
    clipped = stripped[: max(0, max_chars - 3)].rstrip(" ,;:-")
    return f"{clipped}..."


def _is_empty_context(text: str) -> bool:
    stripped = (text or "").strip()
    return not stripped or stripped in _SECTION_FALLBACKS.values()


def _context_payload_lines(context: str) -> list[str]:
    if _is_empty_context(context):
        return []
    header, bullets = _split_context(context)
    if bullets:
        return bullets
    if header:
        return [header]
    return [line.strip() for line in context.splitlines() if line.strip()]


def _extract_grid_refs(text: str) -> list[str]:
    refs = re.findall(r"\b[A-Z]\d(?:-\d+){0,3}\b", text or "")
    seen: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.append(ref)
    return seen


def _compact_contact_line(line: str) -> str:
    stripped = line.strip()
    grid_refs = _extract_grid_refs(stripped)
    confidence_match = re.search(r"conf[=\s]*(\d+%?)", stripped, re.IGNORECASE)
    if " at " in stripped:
        name = stripped.split(" at ", 1)[0].strip()
    else:
        name = stripped
    parts = [name]
    if grid_refs:
        parts.append(f"@{grid_refs[0]}")
    if confidence_match:
        parts.append(f"conf={confidence_match.group(1)}")
    return _truncate(" ".join(parts), 96)


def _compact_map_object_line(line: str) -> str:
    stripped = line.strip()
    name = stripped.split(" (", 1)[0].strip()
    obj_type_match = re.search(r"\(([^,)]+)", stripped)
    obj_type = obj_type_match.group(1).strip() if obj_type_match else ""
    grid_refs = _extract_grid_refs(stripped)
    compact = name
    if obj_type:
        compact += f"[{obj_type}]"
    if grid_refs:
        compact += f"@{grid_refs[0]}"
    return _truncate(compact, 96)


def _compact_terrain_line(line: str) -> str:
    stripped = line.strip()
    if ":" not in stripped:
        return _truncate(stripped, 96)
    unit_or_topic, details = stripped.split(":", 1)
    grid_refs = _extract_grid_refs(details)
    terrain_match = re.search(r"\b([a-z_]+|[а-яё]+)\b", details.strip(), re.IGNORECASE)
    compact = f"{unit_or_topic.strip()}: {details.strip()}"
    if terrain_match and grid_refs:
        compact = f"{unit_or_topic.strip()}: {terrain_match.group(1)}@{grid_refs[0]}"
    return _truncate(compact, 96)


def _compact_status_line(line: str) -> str:
    stripped = line.strip()
    if ":" not in stripped:
        return _truncate(stripped, 96)
    name, details = stripped.split(":", 1)
    detail_parts = []
    for field in ("strength=", "ammo=", "morale=", "task=", "comms="):
        match = re.search(rf"{re.escape(field)}([^,]+)", details)
        if match:
            detail_parts.append(f"{field[:-1]}={match.group(1).strip()}")
    compact = f"{name.strip()}: {', '.join(detail_parts)}" if detail_parts else stripped
    return _truncate(compact, 96)


def _compact_order_line(line: str) -> str:
    stripped = re.sub(r"^\d{4}-\d{2}-\d{2}T[^:]+:\s*", "", line.strip())
    stripped = stripped.split("|", 1)[0].strip()
    return _truncate(stripped, 110)


def _compact_radio_line(line: str) -> str:
    stripped = re.sub(r"^\d{4}-\d{2}-\d{2}T[^\s]+\s*", "", line.strip())
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", stripped)
    return _truncate(stripped, 110)


def _compact_report_line(line: str) -> str:
    stripped = re.sub(r"^\d{4}-\d{2}-\d{2}T[^\s]+\s*", "", line.strip())
    stripped = re.sub(r"^\[[^\]]+\]\s*", "", stripped)
    stripped = re.sub(r"\bfrom\s+", "", stripped, flags=re.IGNORECASE)
    return _truncate(stripped, 110)


def _compact_section_lines(section_name: str, context: str, limit: int) -> list[str]:
    lines = _context_payload_lines(context)
    if not lines:
        return []

    compactors = {
        "contacts": _compact_contact_line,
        "map_objects": _compact_map_object_line,
        "terrain": _compact_terrain_line,
        "friendly_status": _compact_status_line,
        "orders": _compact_order_line,
        "radio": _compact_radio_line,
        "reports": _compact_report_line,
    }
    compactor = compactors.get(section_name, lambda line: _truncate(line.strip(), 110))
    compacted: list[str] = []
    for line in lines[:limit]:
        item = compactor(line)
        if item and item not in compacted:
            compacted.append(item)
    return compacted


def _build_history_digest(
    *,
    orders_context: str,
    radio_context: str,
    reports_context: str,
    limit: int,
) -> str:
    parts: list[str] = []
    order_lines = _compact_section_lines("orders", orders_context, 1)
    radio_lines = _compact_section_lines("radio", radio_context, 1)
    report_lines = _compact_section_lines("reports", reports_context, 1)
    if order_lines:
        parts.append(f"order={order_lines[0]}")
    if radio_lines:
        parts.append(f"radio={radio_lines[0]}")
    if report_lines:
        parts.append(f"report={report_lines[0]}")
    if not parts:
        return ""
    return _truncate("Recent facts: " + " | ".join(parts[:limit]), 280)


def _unit_atom(unit: dict) -> str:
    task = (unit.get("current_task") or {}).get("type", "idle")
    strength = unit.get("strength")
    ammo = unit.get("ammo")
    morale = unit.get("morale")
    flags = [unit.get("unit_type", "?"), f"task={task}"]
    if strength is not None:
        flags.append(f"str={strength:.0%}")
    if ammo is not None:
        flags.append(f"ammo={ammo:.0%}")
    if morale is not None:
        flags.append(f"morale={morale:.0%}")
    if unit.get("comms_status") and unit.get("comms_status") != "operational":
        flags.append(f"comms={unit['comms_status']}")
    return _truncate(f"{unit.get('name', '?')}[{', '.join(flags)}]", 120)


def _build_continuity_hints(
    *,
    original_text: str,
    parsed_hint: ParsedOrderData,
    orders_context: str,
    radio_context: str,
    reports_context: str,
    contacts_context: str,
    map_objects_context: str,
    limit: int,
) -> str:
    text_lower = (original_text or "").lower()
    hints: list[str] = []

    continue_markers = ("continue", "as before", "same route", "продолж", "как раньше", "по-прежнему")
    target_markers = ("same target", "the target", "that target", "тот же", "ту же цель", "на цель", "по цели")
    object_markers = ("bridge", "crossing", "roadblock", "bunker", "мост", "переправ", "заграж", "дот")

    if any(marker in text_lower for marker in continue_markers):
        recent_order = _compact_section_lines("orders", orders_context, 1)
        if recent_order:
            hints.append(f"continue_from={recent_order[0]}")

    if any(marker in text_lower for marker in target_markers):
        target_contact = _compact_section_lines("contacts", contacts_context, 1)
        if target_contact:
            hints.append(f"same_target={target_contact[0]}")
        else:
            target_object = _compact_section_lines("map_objects", map_objects_context, 1)
            if target_object:
                hints.append(f"same_target={target_object[0]}")

    if (
        not parsed_hint.location_refs
        and any(marker in text_lower for marker in object_markers)
    ):
        object_candidates = _compact_section_lines("map_objects", map_objects_context, 2)
        if object_candidates:
            hints.append(f"object_candidates={'; '.join(object_candidates)}")

    recent_radio = _compact_section_lines("radio", radio_context, 1)
    if recent_radio and any(marker in text_lower for marker in ("report", "долож", "confirm", "подтверд")):
        hints.append(f"latest_radio={recent_radio[0]}")

    recent_report = _compact_section_lines("reports", reports_context, 1)
    if recent_report and parsed_hint.order_type and parsed_hint.order_type.value in {"request_fire", "fire", "observe"}:
        hints.append(f"latest_report={recent_report[0]}")

    if not hints:
        return ""

    lines = ["Continuity hints:"]
    for hint in hints[:limit]:
        lines.append(f"  - {hint}")
    return "\n".join(lines)


def _build_state_packet(
    *,
    original_text: str,
    parsed_hint: ParsedOrderData,
    units_for_prompt: list[dict],
    selected_sections: dict[str, str],
    height_tops_context: str,
    history_digest: str,
    continuity_hints: str,
    max_chars: int,
) -> str:
    task_bits = [
        f"class={parsed_hint.classification.value}",
        f"lang={parsed_hint.language.value}",
    ]
    if parsed_hint.order_type:
        task_bits.append(f"order_hint={parsed_hint.order_type.value}")
    if parsed_hint.sender_ref:
        task_bits.append(f"sender={parsed_hint.sender_ref}")
    if parsed_hint.target_unit_refs:
        task_bits.append(f"targets={','.join(parsed_hint.target_unit_refs[:3])}")
    if parsed_hint.coordination_unit_refs:
        task_bits.append(f"coord={','.join(parsed_hint.coordination_unit_refs[:2])}")
    if parsed_hint.support_target_ref:
        task_bits.append(f"support={parsed_hint.support_target_ref}")
    if parsed_hint.merge_target_ref:
        task_bits.append(f"merge={parsed_hint.merge_target_ref}")
    if parsed_hint.map_object_type:
        task_bits.append(f"object={parsed_hint.map_object_type}")
    if parsed_hint.purpose:
        task_bits.append(f"purpose={_truncate(parsed_hint.purpose, 60)}")

    location_values: list[str] = []
    for loc in parsed_hint.location_refs or []:
        normalized = loc.normalized or loc.source_text
        if normalized:
            location_values.append(f"{normalized}/{loc.ref_type}")

    lines = [
        "Parser state packet:",
        f"task: {'; '.join(task_bits)}",
    ]
    if location_values:
        lines.append(f"locations: {', '.join(location_values[:4])}")
    if units_for_prompt:
        unit_atoms = [_unit_atom(unit) for unit in units_for_prompt[:4]]
        lines.append(f"units: {'; '.join(unit_atoms)}")

    packet_mappings = [
        ("contacts", "contacts"),
        ("map_objects", "objects"),
        ("terrain", "terrain"),
        ("friendly_status", "friendlies"),
        ("objectives", "mission"),
        ("environment", "environment"),
    ]
    for section_name, label in packet_mappings:
        compact_lines = _compact_section_lines(section_name, selected_sections[f"{section_name}_context"], 3)
        if compact_lines:
            lines.append(f"{label}: {'; '.join(compact_lines)}")

    height_lines = _context_payload_lines(height_tops_context)
    if height_lines:
        lines.append(f"heights: {'; '.join(_truncate(line, 60) for line in height_lines[:3])}")

    if history_digest:
        lines.append(history_digest)

    if continuity_hints:
        hint_lines = _context_payload_lines(continuity_hints)
        if hint_lines:
            lines.append(f"continuity: {'; '.join(_truncate(line, 72) for line in hint_lines[:3])}")

    packet = "\n".join(lines)
    return _truncate(packet, max_chars)


def _select_relevant_context(
    section_name: str,
    context: str,
    *,
    query_tokens: set[str],
    section_keywords: list[str],
    max_lines: int,
) -> str:
    if not context:
        return _SECTION_FALLBACKS[section_name]

    header, bullets = _split_context(context)
    if not bullets:
        compact = "\n".join(line.strip() for line in context.splitlines()[:max_lines] if line.strip())
        return compact or _SECTION_FALLBACKS[section_name]

    prefer_recent = section_name in {"radio", "orders", "reports"}
    scored = [
        (
            _score_line(line, query_tokens, section_keywords, idx, len(bullets), prefer_recent=prefer_recent),
            idx,
            line,
        )
        for idx, line in enumerate(bullets)
    ]
    top = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_lines]
    selected = [line for _, _, line in sorted(top, key=lambda item: item[1])]
    if not selected:
        return _SECTION_FALLBACKS[section_name]

    lines: list[str] = []
    if header:
        lines.append(header)
    lines.extend(f"  - {line}" for line in selected)
    return "\n".join(lines)


def _build_query_tokens(original_text: str, parsed_hint: ParsedOrderData, profile_keywords: list[str]) -> set[str]:
    tokens = set(_tokenize(original_text))
    tokens.update(profile_keywords)
    tokens.update(_tokenize(parsed_hint.sender_ref or ""))
    for value in (
        *(parsed_hint.target_unit_refs or []),
        *(parsed_hint.coordination_unit_refs or []),
        parsed_hint.support_target_ref or "",
        parsed_hint.merge_target_ref or "",
        parsed_hint.map_object_type or "",
        parsed_hint.purpose or "",
    ):
        tokens.update(_tokenize(value))
    for loc in parsed_hint.location_refs or []:
        tokens.update(_tokenize(loc.source_text))
        tokens.update(_tokenize(loc.normalized))
    if parsed_hint.order_type:
        tokens.update(_tokenize(parsed_hint.order_type.value))
    return tokens


def _select_units_for_prompt(
    units: list[dict],
    parsed_hint: ParsedOrderData,
    order_type: str | None,
    limit: int,
) -> list[dict]:
    target_refs = [ref.lower() for ref in (parsed_hint.target_unit_refs or []) if ref]
    coordination_refs = [ref.lower() for ref in (parsed_hint.coordination_unit_refs or []) if ref]
    special_refs = [ref.lower() for ref in [parsed_hint.merge_target_ref, parsed_hint.support_target_ref] if ref]

    def _unit_score(unit: dict) -> float:
        name = (unit.get("name") or "").lower()
        unit_type = (unit.get("unit_type") or "").lower()
        score = 0.0
        if any(ref in name for ref in target_refs):
            score += 10.0
        if any(ref in name for ref in coordination_refs):
            score += 6.0
        if any(ref in name for ref in special_refs):
            score += 5.0
        if unit.get("comms_status") == "offline":
            score -= 2.0
        if unit.get("is_destroyed"):
            score -= 5.0
        if (unit.get("strength") or 1.0) < 0.6:
            score += 1.5
        if order_type in {"fire", "request_fire", "support"} and any(key in unit_type for key in ("mortar", "artillery", "fire")):
            score += 3.0
        if order_type in {"breach", "lay_mines", "construct", "deploy_bridge"} and "engineer" in unit_type:
            score += 3.0
        if order_type == "resupply" and any(key in unit_type for key in ("log", "supply")):
            score += 3.0
        if order_type == "observe" and any(key in unit_type for key in ("recon", "uav", "drone", "sniper")):
            score += 3.0
        if order_type in {"move", "attack"} and unit.get("current_task"):
            score += 0.5
        return score

    ranked = sorted(units, key=lambda unit: (-_unit_score(unit), unit.get("name", "")))
    return ranked[:limit]


def _select_height_tops_context(
    grid_info: dict | None,
    original_text: str,
    parsed_hint: ParsedOrderData,
    limit: int,
) -> str:
    if not grid_info or not grid_info.get("height_tops"):
        return ""

    text_lower = (original_text or "").lower()
    needs_height = any(
        term in text_lower
        for term in ("height", "hill", "высота", "выс.", "отметка")
    ) or any((loc.ref_type == "height") for loc in (parsed_hint.location_refs or []))
    if not needs_height:
        return ""

    lines = ["Relevant height tops:"]
    for peak in grid_info["height_tops"][:limit]:
        label = peak.get("label_ru") or peak.get("label") or "height"
        snail = peak.get("snail_path", "?")
        elev = peak.get("elevation_m")
        elev_str = f", {elev:.0f}m" if elev is not None else ""
        lines.append(f"  - {label} ({snail}{elev_str})")
    return "\n".join(lines)


def build_order_parser_context(
    *,
    original_text: str,
    parsed_hint: ParsedOrderData,
    doctrine_topics: list[str],
    units: list[dict],
    grid_info: dict | None,
    terrain_context: str,
    contacts_context: str,
    objectives_context: str,
    friendly_status_context: str,
    environment_context: str,
    orders_context: str,
    radio_context: str,
    reports_context: str,
    map_objects_context: str,
    profile: str = "cloud",
) -> RetrievedParserContext:
    order_type = parsed_hint.order_type.value if parsed_hint.order_type else None
    profile_cfg = _PROFILE_CONFIG.get(order_type or "", {})
    profile_topics = list(dict.fromkeys(["general", *(profile_cfg.get("topics") or doctrine_topics or [])]))
    limits = _BASE_LIMITS.get(profile, _BASE_LIMITS["cloud"])
    query_tokens = _build_query_tokens(original_text, parsed_hint, profile_cfg.get("keywords", []))
    section_keywords = profile_cfg.get("section_keywords", {})

    doctrine_text = get_tactical_doctrine_excerpt(
        level="brief",
        topics=profile_topics,
        query=original_text,
        max_passages=limits["doctrine_passages"],
        max_chars=limits["doctrine_chars"],
    )

    units_for_prompt = _select_units_for_prompt(
        units=units,
        parsed_hint=parsed_hint,
        order_type=order_type,
        limit=limits["units"],
    )

    height_tops_context = _select_height_tops_context(
        grid_info=grid_info,
        original_text=original_text,
        parsed_hint=parsed_hint,
        limit=limits["height_tops"],
    )

    selected_sections = {
        "terrain_context": _select_relevant_context(
            "terrain",
            terrain_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("terrain", []),
            max_lines=limits["terrain"],
        ),
        "contacts_context": _select_relevant_context(
            "contacts",
            contacts_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("contacts", []),
            max_lines=limits["contacts"],
        ),
        "objectives_context": _select_relevant_context(
            "objectives",
            objectives_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("objectives", []),
            max_lines=limits["objectives"],
        ),
        "friendly_status_context": _select_relevant_context(
            "friendly_status",
            friendly_status_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("friendly_status", []),
            max_lines=limits["friendly_status"],
        ),
        "environment_context": _select_relevant_context(
            "environment",
            environment_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("environment", []),
            max_lines=limits["environment"],
        ),
        "orders_context": _select_relevant_context(
            "orders",
            orders_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("orders", []),
            max_lines=limits["orders"],
        ),
        "radio_context": _select_relevant_context(
            "radio",
            radio_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("radio", []),
            max_lines=limits["radio"],
        ),
        "reports_context": _select_relevant_context(
            "reports",
            reports_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("reports", []),
            max_lines=limits["reports"],
        ),
        "map_objects_context": _select_relevant_context(
            "map_objects",
            map_objects_context,
            query_tokens=query_tokens,
            section_keywords=section_keywords.get("map_objects", []),
            max_lines=limits["map_objects"],
        ),
    }

    history_digest = _build_history_digest(
        orders_context=selected_sections["orders_context"],
        radio_context=selected_sections["radio_context"],
        reports_context=selected_sections["reports_context"],
        limit=limits["history_facts"],
    )
    continuity_hints = _build_continuity_hints(
        original_text=original_text,
        parsed_hint=parsed_hint,
        orders_context=selected_sections["orders_context"],
        radio_context=selected_sections["radio_context"],
        reports_context=selected_sections["reports_context"],
        contacts_context=selected_sections["contacts_context"],
        map_objects_context=selected_sections["map_objects_context"],
        limit=limits["continuity_facts"],
    )
    state_packet = _build_state_packet(
        original_text=original_text,
        parsed_hint=parsed_hint,
        units_for_prompt=units_for_prompt,
        selected_sections=selected_sections,
        height_tops_context=height_tops_context,
        history_digest=history_digest,
        continuity_hints=continuity_hints,
        max_chars=limits["state_packet_chars"],
    )

    return RetrievedParserContext(
        doctrine_text=doctrine_text,
        doctrine_topics=profile_topics,
        units_for_prompt=units_for_prompt,
        state_packet=state_packet,
        history_digest=history_digest,
        continuity_hints=continuity_hints,
        height_tops_context=height_tops_context,
        terrain_context=selected_sections["terrain_context"],
        contacts_context=selected_sections["contacts_context"],
        objectives_context=selected_sections["objectives_context"],
        friendly_status_context=selected_sections["friendly_status_context"],
        environment_context=selected_sections["environment_context"],
        orders_context=selected_sections["orders_context"],
        radio_context=selected_sections["radio_context"],
        reports_context=selected_sections["reports_context"],
        map_objects_context=selected_sections["map_objects_context"],
    )
