"""
Templates for generating unit radio responses.

Two modes:
1. Template-based (instant, no LLM) — for standard ack/nack/unable
2. LLM-enhanced (optional) — for richer status reports and clarifications
"""

from __future__ import annotations

import random

# ── Template-based responses (keyed by response_type × language) ──

TEMPLATES_RU = {
    "ack": [
        "Здесь {unit}. Так точно, принял. {status_text}",
        "Здесь {unit}, приём. Понял, выполняю. {status_text}",
        "{unit}, приказ принят. {status_text}",
        "Здесь {unit}. Вас понял, приступаю. {status_text}",
    ],
    "wilco": [
        "Здесь {unit}. Так точно, выполняем. {status_text}",
        "{unit} принял. Начали движение. {status_text}",
        "Здесь {unit}. Приказ принят, выдвигаемся. {status_text}",
    ],
    "wilco_fire": [
        "Здесь {unit}. Принял, готовлю огонь. {status_text}",
        "{unit}, приём. Цель принята, готовимся к открытию огня. {status_text}",
        "Здесь {unit}. Так точно, расчёт к бою. {status_text}",
        "{unit} принял. Огонь по готовности. {status_text}",
    ],
    "wilco_disengage": [
        "Здесь {unit}. Принял, разрываем контакт! {status_text}",
        "{unit}, приём. Выходим из боя, ищем укрытие. {status_text}",
        "Здесь {unit}. Так точно, отрываемся от противника. {status_text}",
        "{unit} принял. Прекращаем огонь, уходим в укрытие. {status_text}",
    ],
    "wilco_resupply": [
        "Здесь {unit}. Принял, выдвигаемся на пополнение. {status_text}",
        "{unit}, приём. Так точно, следуем к пункту снабжения. {status_text}",
        "Здесь {unit}. Принял. Выходим на пополнение боеприпасов. {status_text}",
        "{unit} принял. Начинаем пополнение запасов. {status_text}",
    ],
    "wilco_observe": [
        "Здесь {unit}. Принял, занимаю позицию. {status_text}",
        "{unit}, приём. Так точно, веду наблюдение. {status_text}",
        "Здесь {unit}. Принял. На позиции. {status_text}",
        "{unit} принял. Занимаем оборону. {status_text}",
    ],
    "wilco_standby": [
        "Здесь {unit}. Принял, в готовности. {status_text}",
        "{unit}, приём. Так точно, на связи с {support_target}. В готовности поддержать по команде. {status_text}",
        "Здесь {unit}. Принял. Жду целеуказания от {support_target}. {status_text}",
        "{unit} принял. В готовности поддержать {support_target}. Жду команды. {status_text}",
    ],
    "unable": [
        "Здесь {unit}. Не могу выполнить! {reason}",
        "{unit}, приём. Выполнение невозможно. {reason}",
        "Здесь {unit}. Отказ. {reason}",
    ],
    "unable_range": [
        "Здесь {unit}. Цель за пределами дальности. {status_text} Выдвигаюсь на огневую позицию.",
        "{unit}, приём. Не достаю — {status_text} Начинаю выдвижение на позицию ближе к цели.",
        "Здесь {unit}. Дальность недостаточна. {status_text} Перемещаюсь на огневой рубеж.",
    ],
    "unable_area": [
        "Здесь {unit}. Не могу выполнить! {status_text}",
        "{unit}, приём. Цель за пределами района операции. Запрашиваю уточнение.",
        "Здесь {unit}. Указанные координаты вне зоны ответственности. {status_text}",
    ],
    "unable_route": [
        "Здесь {unit}. Маршрут до указанной точки невозможен. Местность непроходима. {status_text}",
        "{unit}, приём. Не могу выполнить — нет проходимого маршрута к указанной позиции. {status_text}",
        "Здесь {unit}. Отказ. Маршрут заблокирован, необходим альтернативный маршрут или инженерное обеспечение. {status_text}",
    ],
    "clarify": [
        "Здесь {unit}. Не понял приказ. Повторите, приём!",
        "{unit}, приём. Приказ неясен. Уточните задачу!",
        "Здесь {unit}. Не разобрал. Повторите приказ, приём!",
        "Здесь {unit}. Что? Не понял. Повторите приказ чётко!",
    ],
    "status": [
        "Здесь {unit}, приём. {status_text}",
        "{unit} докладывает: {status_text}",
    ],
    "no_response": [],  # silence
    "morale_broken": [
        "Здесь {unit}... У нас тяжёлые потери... Отступаем!",
        "{unit}... Не можем... Выходим из боя!",
        "Здесь {unit}. Мы разбиты. Отходим!",
    ],
    "comms_degraded": [
        "Здесь {unit}... *помехи*... при...нял... *помехи*... выполн...",
        "{unit}... *шум*... понял... *треск*...",
    ],
}

TEMPLATES_EN = {
    "ack": [
        "This is {unit}. Roger, copy. Executing. {status_text}",
        "{unit} here. Acknowledged, moving to execute. {status_text}",
        "{unit}, roger. Wilco. {status_text}",
        "This is {unit}. Copy that, commencing. {status_text}",
    ],
    "wilco": [
        "This is {unit}. Roger, wilco. {status_text}",
        "{unit}, copy. Beginning movement. {status_text}",
        "This is {unit}. Order received, executing now. {status_text}",
    ],
    "wilco_fire": [
        "This is {unit}. Roger, preparing to fire. {status_text}",
        "{unit}, copy. Target received, standing by to fire. {status_text}",
        "This is {unit}. Fire mission received, crew ready. {status_text}",
        "{unit}, shot out. Firing for effect. {status_text}",
    ],
    "wilco_disengage": [
        "This is {unit}. Roger, breaking contact! {status_text}",
        "{unit}, copy. Disengaging, seeking cover. {status_text}",
        "This is {unit}. Wilco, pulling back to covered position. {status_text}",
        "{unit}, breaking contact now. Moving to cover. {status_text}",
    ],
    "wilco_resupply": [
        "This is {unit}. Roger, moving to resupply point. {status_text}",
        "{unit}, copy. Heading to supply cache for rearm. {status_text}",
        "This is {unit}. Wilco, proceeding to resupply. {status_text}",
        "{unit}, moving to logistics point for ammunition resupply. {status_text}",
    ],
    "wilco_observe": [
        "This is {unit}. Roger, taking position. {status_text}",
        "{unit}, copy. In position, observing. {status_text}",
        "This is {unit}. Roger, holding position. {status_text}",
        "{unit}, in position. Standing by. {status_text}",
    ],
    "wilco_standby": [
        "This is {unit}. Roger, standing by. {status_text}",
        "{unit}, copy. Linked with {support_target}. Standing by to support on call. {status_text}",
        "This is {unit}. Roger, awaiting target designation from {support_target}. {status_text}",
        "{unit}, standing by to support {support_target}. Awaiting fire command. {status_text}",
    ],
    "unable": [
        "This is {unit}. Unable to comply! {reason}",
        "{unit} here. Cannot execute. {reason}",
        "This is {unit}. Negative. {reason}",
    ],
    "unable_range": [
        "This is {unit}. Target beyond maximum range. {status_text} Repositioning to firing position.",
        "{unit} here. Out of range — {status_text} Moving to engagement range.",
        "This is {unit}. Cannot engage from current position. {status_text} Advancing to firing position.",
    ],
    "unable_area": [
        "This is {unit}. Cannot comply! {status_text}",
        "{unit} here. Target is outside the area of operations. Requesting corrected coordinates.",
        "This is {unit}. Designated location outside our AO. {status_text}",
    ],
    "unable_route": [
        "This is {unit}. Route to designated position impassable. Terrain does not permit passage. {status_text}",
        "{unit} here. No viable route to objective. Terrain blocked. Requesting alternate coordinates. {status_text}",
        "This is {unit}. Cannot comply — route impassable. Require engineer support or alternate route. {status_text}",
    ],
    "clarify": [
        "This is {unit}. Say again? Orders unclear. Please repeat, over.",
        "{unit} here. Did not copy. Requesting clarification, over.",
        "This is {unit}. Unclear order. Say again all after, over.",
        "This is {unit}. Cannot comply — orders not understood. Please repeat clearly, over.",
    ],
    "status": [
        "This is {unit}, over. {status_text}",
        "{unit} reports: {status_text}",
    ],
    "no_response": [],
    "morale_broken": [
        "This is {unit}... Heavy casualties... We're pulling back!",
        "{unit}... Can't hold... Breaking contact!",
        "This is {unit}. We're done. Falling back!",
    ],
    "comms_degraded": [
        "This is {unit}... *static*... rog... *static*... exec...",
        "{unit}... *noise*... copy... *break*...",
    ],
}

# Reasons for inability (keyed by cause)
UNABLE_REASONS_RU = {
    "destroyed": "Подразделение уничтожено.",
    "morale_broken": "Потеряли боеспособность, отступаем!",
    "no_ammo": "Боеприпасы на нуле!",
    "heavy_casualties": "Тяжёлые потери, не в состоянии выполнить.",
    "out_of_range": "",  # filled dynamically with distance/bearing info
}

UNABLE_REASONS_EN = {
    "destroyed": "Unit destroyed.",
    "morale_broken": "Lost combat capability, falling back!",
    "no_ammo": "Ammunition depleted!",
    "heavy_casualties": "Heavy casualties, unable to comply.",
    "out_of_range": "",  # filled dynamically with distance/bearing info
}


def get_template_response(
    unit_name: str,
    response_type: str,
    language: str = "en",
    reason_key: str | None = None,
    status_text: str = "",
    support_target: str = "",
) -> str | None:
    """
    Pick a random template response.

    Returns None for no_response type (comms offline / destroyed).
    """
    templates = TEMPLATES_RU if language == "ru" else TEMPLATES_EN

    if response_type == "no_response":
        return None

    # Special cases
    if response_type == "morale_broken":
        pool = templates.get("morale_broken", [])
    elif response_type == "comms_degraded":
        pool = templates.get("comms_degraded", [])
    else:
        pool = templates.get(response_type, templates.get("ack", []))

    if not pool:
        return None

    # For standby templates, prefer templates with {support_target} if we have one
    if response_type == "wilco_standby" and support_target:
        with_target = [t for t in pool if "{support_target}" in t]
        if with_target:
            pool = with_target
    elif response_type == "wilco_standby" and not support_target:
        # No support target → use templates without {support_target}
        without_target = [t for t in pool if "{support_target}" not in t]
        if without_target:
            pool = without_target

    template = random.choice(pool)

    # Fill in placeholders
    reasons = UNABLE_REASONS_RU if language == "ru" else UNABLE_REASONS_EN
    reason = reasons.get(reason_key, "") if reason_key else ""

    return template.format(
        unit=unit_name,
        reason=reason,
        status_text=status_text,
        support_target=support_target or "",
    ).strip()


# ── LLM prompt for richer responses (optional, Phase 2 enhancement) ──

LLM_RESPONSE_SYSTEM_PROMPT = """You are a military unit radio operator responding to commands in a tactical exercise.
You respond in character, using military radio protocol.

Your unit: {unit_name} ({unit_type})
Unit status: strength {strength}%, morale {morale}%, suppression {suppression}%, comms: {comms_status}
Current task: {current_task}
Language: {language}

Respond to the message with a brief, realistic military radio response (1-2 sentences).
Use proper radio protocol for the language (Russian or English).
If Russian, use authentic military radio style.
"""

