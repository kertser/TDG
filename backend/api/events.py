"""Events API – query game event log + replay data."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.event import Event
from backend.models.unit import Unit
from backend.models.order import Order
from backend.models.report import Report
from backend.models.session import Session

router = APIRouter()


@router.get("/{session_id}/events")
async def list_events(
    session_id: uuid.UUID,
    since_tick: int | None = Query(None),
    event_type: str | None = Query(None, alias="type"),
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    query = select(Event).where(Event.session_id == session_id)
    if since_tick is not None:
        query = query.where(Event.tick >= since_tick)
    if event_type:
        query = query.where(Event.event_type == event_type)
    query = query.order_by(Event.tick.asc(), Event.created_at.asc())

    result = await db.execute(query)
    events = result.scalars().all()

    # Filter by visibility
    side = participant.side.value
    visible = []
    for e in events:
        vis = e.visibility.value
        if vis == "all" or vis == side or side in ("admin", "observer"):
            visible.append({
                "id": str(e.id),
                "tick": e.tick,
                "event_type": e.event_type,
                "text_summary": e.text_summary,
                "payload": e.payload,
                "visibility": vis,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "game_timestamp": e.game_timestamp.isoformat() if e.game_timestamp else None,
            })
    return visible


@router.get("/{session_id}/replay")
async def get_replay_data(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return full replay data with per-tick unit position snapshots."""
    side = participant.side.value

    sess_result = await db.execute(select(Session).where(Session.id == session_id))
    session = sess_result.scalar_one_or_none()
    if not session:
        return {"error": "Session not found"}

    # Get all events
    ev_result = await db.execute(
        select(Event).where(Event.session_id == session_id)
        .order_by(Event.tick.asc(), Event.created_at.asc())
    )
    events = ev_result.scalars().all()

    # Get all units (for initial positions from scenario)
    unit_result = await db.execute(
        select(Unit).where(Unit.session_id == session_id)
    )
    units = unit_result.scalars().all()

    # Get orders
    ord_result = await db.execute(
        select(Order).where(Order.session_id == session_id)
        .order_by(Order.issued_at.asc())
    )
    orders = ord_result.scalars().all()

    # Get reports
    rep_result = await db.execute(
        select(Report).where(Report.session_id == session_id)
        .order_by(Report.tick.asc(), Report.created_at.asc())
    )
    reports = rep_result.scalars().all()

    max_tick = session.tick or 0

    # ── Build initial unit positions (tick 0) ──
    from geoalchemy2.shape import to_shape
    unit_positions = {}  # unit_id -> {lat, lon}
    units_info = {}  # unit_id -> static info
    for u in units:
        uid = str(u.id)
        pos = None
        if u.position is not None:
            try:
                pt = to_shape(u.position)
                pos = {"lat": pt.y, "lon": pt.x}
            except Exception:
                pass
        units_info[uid] = {
            "id": uid,
            "name": u.name,
            "side": u.side.value if u.side else "blue",
            "unit_type": u.unit_type,
            "sidc": u.sidc,
            "is_destroyed": u.is_destroyed,
        }
        if pos:
            unit_positions[uid] = pos

    # ── Scan events to extract per-tick unit positions ──
    # Movement events contain the new lat/lon after movement
    # We also track strength changes and destroyed status
    tick_unit_updates = {}  # tick -> { unit_id -> {lat, lon, strength, is_destroyed} }
    tick_events = {}  # tick -> [event dicts]
    unit_strengths = {str(u.id): u.strength for u in units}
    unit_destroyed = {str(u.id): False for u in units}

    for e in events:
        vis = e.visibility.value
        if vis != "all" and vis != side and side not in ("admin", "observer"):
            continue
        t = e.tick or 0
        if t not in tick_events:
            tick_events[t] = []
        tick_events[t].append({
            "event_type": e.event_type,
            "text_summary": e.text_summary,
            "payload": e.payload,
        })

        payload = e.payload or {}
        # Extract unit position updates from movement-related events
        if e.event_type in ("movement", "order_completed", "movement_completed",
                            "task_completed", "arrived"):
            uid = str(payload.get("unit_id") or e.actor_unit_id or "")
            # Movement events store position in payload.to.{lat,lon}
            if "to" in payload and isinstance(payload["to"], dict):
                lat = payload["to"].get("lat")
                lon = payload["to"].get("lon")
            else:
                lat = payload.get("lat")
                lon = payload.get("lon")
            if uid and lat is not None and lon is not None:
                if t not in tick_unit_updates:
                    tick_unit_updates[t] = {}
                tick_unit_updates[t][uid] = {
                    "lat": lat, "lon": lon,
                }
                unit_positions[uid] = {"lat": lat, "lon": lon}

        # Track destroyed units
        if e.event_type == "unit_destroyed":
            uid = str(payload.get("unit_id") or e.target_unit_id or "")
            if uid:
                unit_destroyed[uid] = True
                if t not in tick_unit_updates:
                    tick_unit_updates[t] = {}
                tick_unit_updates[t].setdefault(uid, {})
                tick_unit_updates[t][uid]["is_destroyed"] = True

        # Track combat damage
        if e.event_type == "combat":
            target_id = str(payload.get("target_unit_id") or e.target_unit_id or "")
            dmg = payload.get("damage") or payload.get("dmg")
            target_lat = payload.get("target_lat")
            target_lon = payload.get("target_lon")
            if target_id and target_lat is not None:
                if t not in tick_unit_updates:
                    tick_unit_updates[t] = {}
                tick_unit_updates[t].setdefault(target_id, {})
                tick_unit_updates[t][target_id]["lat"] = target_lat
                tick_unit_updates[t][target_id]["lon"] = target_lon
                unit_positions[target_id] = {"lat": target_lat, "lon": target_lon}

    # ── Build per-tick snapshots ──
    # Tick 0: initial positions (all units from scenario)
    # Each subsequent tick: accumulate position changes
    running_positions = {}
    running_destroyed = {}

    # Get initial positions from scenario (we need to infer from the data)
    # Use reverse approach: start from final positions and subtract movement
    # Actually simpler: for tick 0 use initial_units from scenario, then apply events forward

    # Build snapshots from events forward
    # First: get scenario initial positions
    from backend.models.scenario import Scenario
    scenario_result = await db.execute(
        select(Scenario).where(Scenario.id == session.scenario_id)
    )
    scenario = scenario_result.scalar_one_or_none()
    initial_positions = {}
    if scenario and scenario.initial_units:
        # Scenario stores units as {"blue": [...], "red": [...]} or flat list
        name_to_initial_pos = {}
        iu_data = scenario.initial_units
        all_iu = []
        if isinstance(iu_data, dict):
            for side_key in ("blue", "red"):
                all_iu.extend(iu_data.get(side_key, []))
        elif isinstance(iu_data, list):
            all_iu = iu_data
        for iu in all_iu:
            name = iu.get("name")
            lat = iu.get("lat")
            lon = iu.get("lon")
            if name and lat is not None and lon is not None:
                name_to_initial_pos[name] = {"lat": lat, "lon": lon}
        # Match DB units by name
        for uid, info in units_info.items():
            if info["name"] in name_to_initial_pos:
                initial_positions[uid] = name_to_initial_pos[info["name"]]

    # Initialize running state with current DB positions (which is the final state)
    # We'll reconstruct backwards -- but that's complex. Instead, build forward:
    # For units we don't have initial positions for, use first known position
    for uid, info in units_info.items():
        running_destroyed[uid] = False

    # Build tick-by-tick position snapshots
    ticks_data = {}
    # First, find earliest known positions per unit from events
    first_positions = {}
    for t in sorted(tick_unit_updates.keys()):
        for uid, upd in tick_unit_updates[t].items():
            if uid not in first_positions and "lat" in upd:
                first_positions[uid] = {"lat": upd["lat"], "lon": upd["lon"]}

    # Initialize with initial_positions or first known event position, or current DB pos
    for uid in units_info:
        if uid in initial_positions:
            running_positions[uid] = initial_positions[uid].copy()
        elif uid in first_positions:
            running_positions[uid] = first_positions[uid].copy()
        elif uid in unit_positions:
            running_positions[uid] = unit_positions[uid].copy()

    for tick in range(0, max_tick + 1):
        # Apply updates for this tick
        if tick in tick_unit_updates:
            for uid, upd in tick_unit_updates[tick].items():
                if "lat" in upd and "lon" in upd:
                    running_positions[uid] = {"lat": upd["lat"], "lon": upd["lon"]}
                if upd.get("is_destroyed"):
                    running_destroyed[uid] = True

        # Build snapshot
        unit_snapshot = []
        for uid, info in units_info.items():
            pos = running_positions.get(uid)
            if not pos:
                continue
            unit_snapshot.append({
                "id": uid,
                "name": info["name"],
                "side": info["side"],
                "sidc": info["sidc"],
                "unit_type": info["unit_type"],
                "lat": pos["lat"],
                "lon": pos["lon"],
                "is_destroyed": running_destroyed.get(uid, False),
            })

        ticks_data[tick] = {
            "units": unit_snapshot,
            "events": tick_events.get(tick, []),
            "orders": [],
            "reports": [],
        }

    # Add orders to ticks
    for o in orders:
        tick_est = 0
        for e in events:
            if e.event_type == "order_issued" and e.payload and str(e.payload.get("order_id", "")) == str(o.id):
                tick_est = e.tick or 0
                break
        if tick_est in ticks_data:
            ticks_data[tick_est]["orders"].append({
                "original_text": o.original_text,
                "order_type": o.order_type,
                "status": o.status.value if o.status else "pending",
                "issued_by_side": o.issued_by_side.value if o.issued_by_side else "blue",
            })

    # Add reports to ticks
    for r in reports:
        t = r.tick or 0
        if t in ticks_data:
            ticks_data[t]["reports"].append({
                "channel": r.channel,
                "text": r.text,
            })

    return {
        "session_id": str(session_id),
        "max_tick": max_tick,
        "current_time": session.current_time.isoformat() if session.current_time else None,
        "tick_interval": session.tick_interval or 60,
        "ticks": ticks_data,
    }


class AARRequest(BaseModel):
    language: str | None = None  # "en" or "ru" — overrides auto-detection


@router.post("/{session_id}/aar")
async def generate_aar(
    session_id: uuid.UUID,
    request: AARRequest = AARRequest(),
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Generate a professional military After-Action Report using LLM."""
    side = participant.side.value

    sess_result = await db.execute(select(Session).where(Session.id == session_id))
    session = sess_result.scalar_one_or_none()
    if not session:
        return {"error": "Session not found"}

    # Load scenario for context
    from backend.models.scenario import Scenario
    scenario = None
    if session.scenario_id:
        sc_result = await db.execute(select(Scenario).where(Scenario.id == session.scenario_id))
        scenario = sc_result.scalar_one_or_none()

    # ── Detect language: request body > order_service cache > scenario environment ──
    lang = "en"
    if scenario and isinstance(scenario.environment, dict):
        env_lang = scenario.environment.get("language", "en")
        if str(env_lang).lower() in ("ru", "rus", "russian", "русский"):
            lang = "ru"
    # Also check session-level language override via order_service cache
    try:
        from backend.services.order_service import get_session_language
        side_lang = get_session_language(session_id, side if side in ("blue", "red") else "blue")
        if side_lang in ("ru", "rus"):
            lang = "ru"
    except Exception:
        pass
    # Explicit client-provided language always wins
    if request.language and str(request.language).lower() in ("ru", "rus", "russian", "русский"):
        lang = "ru"
    elif request.language and str(request.language).lower() in ("en", "eng", "english"):
        lang = "en"

    ev_result = await db.execute(
        select(Event).where(Event.session_id == session_id)
        .order_by(Event.tick.asc())
    )
    events = ev_result.scalars().all()

    unit_result = await db.execute(
        select(Unit).where(Unit.session_id == session_id)
    )
    units = unit_result.scalars().all()

    ord_result = await db.execute(
        select(Order).where(Order.session_id == session_id)
        .order_by(Order.issued_at.asc())
    )
    orders = ord_result.scalars().all()

    blue_units = [u for u in units if u.side and u.side.value == "blue"]
    red_units = [u for u in units if u.side and u.side.value == "red"]
    blue_destroyed = sum(1 for u in blue_units if u.is_destroyed)
    red_destroyed = sum(1 for u in red_units if u.is_destroyed)

    # Build detailed event timeline
    key_events = []
    for e in events:
        vis = e.visibility.value
        if vis == "all" or vis == side or side in ("admin", "observer"):
            if e.text_summary and e.event_type in (
                "combat", "unit_destroyed", "order_issued", "order_completed",
                "contact_new", "morale_break", "artillery_support",
                "contact_during_advance", "game_finished",
            ):
                key_events.append(f"Turn {e.tick}: [{e.event_type}] {e.text_summary}")

    if len(key_events) > 150:
        key_events = key_events[-150:]

    # Build orders summary
    orders_summary = []
    for o in orders:
        side_str = o.issued_by_side.value if o.issued_by_side else "?"
        orders_summary.append(f"[{side_str}] {o.order_type}: {o.original_text or '(no text)'}")
    if len(orders_summary) > 50:
        orders_summary = orders_summary[-50:]

    # Build unit roster
    blue_roster = "\n".join(
        f"  - {u.name} ({u.unit_type}): strength={u.strength:.0%}, {'DESTROYED' if u.is_destroyed else 'active'}"
        for u in blue_units
    )
    red_roster = "\n".join(
        f"  - {u.name} ({u.unit_type}): strength={u.strength:.0%}, {'DESTROYED' if u.is_destroyed else 'active'}"
        for u in red_units
    )

    scenario_ctx = ""
    if scenario:
        scenario_ctx = f"Scenario: {scenario.title}\n"
        if scenario.description:
            scenario_ctx += f"Description: {scenario.description[:300]}\n"
        if scenario.objectives:
            obj = scenario.objectives
            if isinstance(obj, dict):
                if obj.get("mission"):
                    scenario_ctx += f"Mission: {obj['mission']}\n"
                if obj.get("victory_blue"):
                    scenario_ctx += f"Blue victory condition: {obj['victory_blue']}\n"
                if obj.get("victory_red"):
                    scenario_ctx += f"Red victory condition: {obj['victory_red']}\n"

    context = (
        f"{scenario_ctx}\n"
        f"Exercise duration: {session.tick} turns\n\n"
        f"BLUE FORCE ({len(blue_units)} units, {blue_destroyed} destroyed):\n{blue_roster}\n\n"
        f"RED FORCE ({len(red_units)} units, {red_destroyed} destroyed):\n{red_roster}\n\n"
        f"ORDERS ISSUED ({len(orders_summary)}):\n" + "\n".join(orders_summary) + "\n\n"
        f"KEY EVENTS TIMELINE:\n" + "\n".join(key_events)
    )

    # ── Compute statistics for the report ──
    combat_events = [e for e in events if e.event_type == "combat"]
    contact_events = [e for e in events if e.event_type == "contact_new"]
    destroyed_events = [e for e in events if e.event_type == "unit_destroyed"]
    first_contact_tick = min((e.tick for e in contact_events), default=None)
    first_combat_tick = min((e.tick for e in combat_events), default=None)
    blue_orders = len([o for o in orders if o.issued_by_side and o.issued_by_side.value == "blue"])
    red_orders = len([o for o in orders if o.issued_by_side and o.issued_by_side.value == "red"])

    # Determine winner based on casualties
    blue_casualties_pct = (blue_destroyed / len(blue_units) * 100) if blue_units else 0
    red_casualties_pct = (red_destroyed / len(red_units) * 100) if red_units else 0
    if red_destroyed > 0 and blue_destroyed == 0:
        winner = "BLUE"
        outcome = "Decisive Blue Victory" if lang == "en" else "Решительная победа синих"
    elif blue_destroyed > 0 and red_destroyed == 0:
        winner = "RED"
        outcome = "Decisive Red Victory" if lang == "en" else "Решительная победа красных"
    elif red_casualties_pct > blue_casualties_pct + 20:
        winner = "BLUE"
        outcome = "Blue Tactical Victory" if lang == "en" else "Тактическая победа синих"
    elif blue_casualties_pct > red_casualties_pct + 20:
        winner = "RED"
        outcome = "Red Tactical Victory" if lang == "en" else "Тактическая победа красных"
    else:
        winner = "DRAW"
        outcome = "Inconclusive / Draw" if lang == "en" else "Ничья / Неопределённый результат"

    # ── Build LLM system prompt (bilingual) ──
    if lang == "ru":
        system_prompt = (
            "Вы — старший офицер штаба, составляющий официальный разбор учения (AAR) "
            "для тактического командно-штабного учения. Пишите строго на русском языке, "
            "в профессиональном военном стиле.\n\n"
            "Структурируйте доклад ТОЧНО следующим образом:\n"
            "# РАЗБОР УЧЕНИЯ\n\n"
            "## 1. ОБСТАНОВКА\n"
            "Краткий обзор сценария, задействованных сил и задач.\n\n"
            "## 2. ЗАДАЧА\n"
            "Что должна была выполнить каждая сторона.\n\n"
            "## 3. ХОД ВЫПОЛНЕНИЯ\n"
            "Хронологическое описание проведения операции. Упоминать конкретные подразделения и ходы.\n\n"
            "## 4. РЕЗУЛЬТАТЫ\n"
            "Состояние сил по итогам учения. Потери. Выполнение/невыполнение задач.\n\n"
            "## 5. АНАЛИЗ\n"
            "### а. Что прошло успешно\n"
            "### б. Что прошло неудовлетворительно\n"
            "### в. Ключевые моменты принятия решений\n\n"
            "## 6. ВЫВОДЫ\n"
            "Нумерованный список тактических выводов.\n\n"
            "## 7. РЕКОМЕНДАЦИИ\n"
            "Конкретные рекомендации для будущих операций.\n\n"
            "Используйте военную терминологию. Ссылайтесь на конкретные подразделения и номера ходов. "
            "Будьте аналитичны, не обобщайте. Не более 1000 слов."
        )
    else:
        system_prompt = (
            "You are a senior military staff officer writing a formal After-Action Report (AAR) "
            "for a tactical command-staff exercise. Write in professional military style.\n\n"
            "Structure your report EXACTLY as follows:\n"
            "# AFTER-ACTION REPORT\n\n"
            "## 1. SITUATION\n"
            "Brief overview of the scenario, forces involved, and mission objectives.\n\n"
            "## 2. MISSION\n"
            "What each side was tasked to accomplish.\n\n"
            "## 3. EXECUTION SUMMARY\n"
            "Chronological narrative of how the operation unfolded. Reference specific units and turns.\n\n"
            "## 4. RESULTS\n"
            "Force status at end of exercise. Casualties. Objectives achieved/failed.\n\n"
            "## 5. ANALYSIS\n"
            "### a. What went well\n"
            "### b. What went wrong\n"
            "### c. Key decision points\n\n"
            "## 6. LESSONS LEARNED\n"
            "Numbered list of tactical lessons.\n\n"
            "## 7. RECOMMENDATIONS\n"
            "Specific recommendations for future operations.\n\n"
            "Use military terminology. Reference specific unit names and turn numbers. "
            "Be analytical, not generic. Maximum 1000 words."
        )

    try:
        from backend.services.llm_client import get_llm_client
        llm = get_llm_client()
        if llm is None:
            raise RuntimeError("No LLM configured")

        # Use max_completion_tokens for newer models, fallback to max_tokens
        api_params = {
            "model": llm.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            "temperature": 0.3,
        }

        # Try max_completion_tokens first (for o1/o3/gpt-4.1), fallback to max_tokens
        try:
            api_params["max_completion_tokens"] = 3000
            resp = await llm.client.chat.completions.create(**api_params)
        except Exception as param_err:
            if "max_completion_tokens" in str(param_err) or "unsupported_parameter" in str(param_err):
                del api_params["max_completion_tokens"]
                api_params["max_tokens"] = 3000
                resp = await llm.client.chat.completions.create(**api_params)
            else:
                raise

        aar_text = resp.choices[0].message.content
    except Exception as e:
        # ── Enhanced fallback report with full structure ──
        import logging
        logging.getLogger(__name__).warning("AAR LLM failed: %s", e)

        # Build detailed execution summary from events
        execution_phases = []
        if lang == "ru":
            if first_contact_tick:
                execution_phases.append(f"- **Ход {first_contact_tick}**: Первый контакт с противником")
            if first_combat_tick:
                execution_phases.append(f"- **Ход {first_combat_tick}**: Начало боевых действий")
            for ev in destroyed_events:
                if ev.text_summary:
                    execution_phases.append(f"- **Ход {ev.tick}**: {ev.text_summary}")
        else:
            if first_contact_tick:
                execution_phases.append(f"- **Turn {first_contact_tick}**: First enemy contact established")
            if first_combat_tick:
                execution_phases.append(f"- **Turn {first_combat_tick}**: Combat operations commenced")
            for ev in destroyed_events:
                if ev.text_summary:
                    execution_phases.append(f"- **Turn {ev.tick}**: {ev.text_summary}")

        execution_summary = (
            "\n".join(execution_phases) if execution_phases
            else ("- Значимых боевых событий не зафиксировано" if lang == "ru"
                  else "- No significant combat actions recorded")
        )

        # Build key events list (filter most important)
        filtered_events = [
            line for line in key_events
            if any(kw in line for kw in ["destroyed", "contact_new", "artillery", "morale_break", "order_completed"])
        ][-20:]

        if lang == "ru":
            aar_text = (
                f"# РАЗБОР УЧЕНИЯ\n\n"
                f"---\n"
                f"**Гриф:** ДЛЯ СЛУЖЕБНОГО ПОЛЬЗОВАНИЯ\n"
                f"**Дата:** Завершение учения\n"
                f"**Продолжительность:** {session.tick} ходов\n"
                f"---\n\n"
                f"## 1. ОБСТАНОВКА\n\n"
                f"{scenario_ctx or 'Тактическое командно-штабное учение.'}\n\n"
                f"**Исходная расстановка сил:**\n"
                f"- Синие: {len(blue_units)} подразделений\n"
                f"- Красные: {len(red_units)} подразделений\n\n"
                f"## 2. ЗАДАЧА\n\n"
                f"Синие и красные провели тактическое учение в условиях огневого контакта.\n\n"
                f"## 3. ХОД ВЫПОЛНЕНИЯ\n\n"
                f"{execution_summary}\n\n"
                f"## 4. РЕЗУЛЬТАТЫ\n\n"
                f"### Состояние сил по итогам учения\n\n"
                f"| Сторона | Нач. состав | Выжило | Уничтожено | Потери |\n"
                f"|---------|------------|--------|------------|--------|\n"
                f"| Синие   | {len(blue_units)} | {len(blue_units) - blue_destroyed} | {blue_destroyed} | {blue_casualties_pct:.0f}% |\n"
                f"| Красные | {len(red_units)} | {len(red_units) - red_destroyed} | {red_destroyed} | {red_casualties_pct:.0f}% |\n\n"
                f"### Оценка: **{outcome}**\n\n"
                f"## 5. АНАЛИЗ\n\n"
                f"### а. Тактические показатели\n"
                f"- Первый контакт: ход {first_contact_tick or 'Н/Д'}\n"
                f"- Продолжительность боя: {(session.tick - (first_combat_tick or session.tick))} ходов\n"
                f"- Отдано приказов: синие — {blue_orders}, красные — {red_orders}\n\n"
                f"### б. Ключевые моменты принятия решений\n"
                f"- Исходная расстановка и выдвижение к рубежу встречи\n"
                f"- Решения по вступлению в огневой контакт\n"
                f"- Координация огня и манёвра\n\n"
                f"## 6. ХРОНОЛОГИЯ КЛЮЧЕВЫХ СОБЫТИЙ\n\n"
                + "\n".join(f"- {line}" for line in filtered_events)
                + f"\n\n## 7. ВЫВОДЫ\n\n"
                f"1. Эффективность управления и контроля\n"
                f"2. Организация огневой поддержки\n"
                f"3. Выполнение тактического манёвра\n\n"
                f"---\n"
                f"*ИИ-анализ недоступен ({type(e).__name__})*\n"
                f"*Доклад сформирован по данным учения*"
            )
        else:
            aar_text = (
                f"# AFTER-ACTION REPORT\n\n"
                f"---\n"
                f"**Classification:** UNCLASSIFIED // FOR OFFICIAL USE ONLY\n"
                f"**Date:** Exercise Completion\n"
                f"**Duration:** {session.tick} Turns\n"
                f"---\n\n"
                f"## 1. SITUATION\n\n"
                f"{scenario_ctx or 'Tactical command-staff exercise.'}\n\n"
                f"**Initial Force Disposition:**\n"
                f"- Blue Force: {len(blue_units)} unit(s)\n"
                f"- Red Force: {len(red_units)} unit(s)\n\n"
                f"## 2. MISSION\n\n"
                f"Blue Force and Red Force conducted a tactical engagement exercise.\n\n"
                f"## 3. EXECUTION SUMMARY\n\n"
                f"{execution_summary}\n\n"
                f"## 4. RESULTS\n\n"
                f"### Force Status at Exercise End\n\n"
                f"| Force | Initial | Surviving | Destroyed | Casualty Rate |\n"
                f"|-------|---------|-----------|-----------|---------------|\n"
                f"| Blue  | {len(blue_units)} | {len(blue_units) - blue_destroyed} | {blue_destroyed} | {blue_casualties_pct:.0f}% |\n"
                f"| Red   | {len(red_units)} | {len(red_units) - red_destroyed} | {red_destroyed} | {red_casualties_pct:.0f}% |\n\n"
                f"### Assessment: **{outcome}**\n\n"
                f"## 5. ANALYSIS\n\n"
                f"### a. Tactical Performance\n"
                f"- First contact: Turn {first_contact_tick or 'N/A'}\n"
                f"- Combat duration: {(session.tick - (first_combat_tick or session.tick))} turns\n"
                f"- Orders issued: Blue {blue_orders}, Red {red_orders}\n\n"
                f"### b. Key Decision Points\n"
                f"- Initial disposition and movement to contact\n"
                f"- Engagement decisions upon contact\n"
                f"- Fire and maneuver coordination\n\n"
                f"## 6. KEY EVENTS TIMELINE\n\n"
                + "\n".join(f"- {line}" for line in filtered_events)
                + f"\n\n## 7. LESSONS IDENTIFIED\n\n"
                f"1. Command and control effectiveness\n"
                f"2. Fire support coordination\n"
                f"3. Tactical maneuver execution\n\n"
                f"---\n"
                f"*AI Staff Analysis: Unavailable ({type(e).__name__})*\n"
                f"*Report generated from exercise data*"
            )

    return {"aar": aar_text}


