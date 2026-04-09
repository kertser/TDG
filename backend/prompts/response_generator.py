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
        "Здесь {unit}. Так точно, принял. Выполняю. {status_text}",
        "Здесь {unit}, приём. Понял, выполняю. {status_text}",
        "{unit}, приказ принят. Начинаю выполнение. {status_text}",
        "Здесь {unit}. Вас понял, приступаю. {status_text}",
    ],
    "wilco": [
        "Здесь {unit}. Так точно, выдвигаемся. {status_text}",
        "{unit} принял. Начали движение. {status_text}",
        "Здесь {unit}, выполняем. {status_text}",
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
        "This is {unit}. Roger, moving out. {status_text}",
        "{unit}, copy. Beginning movement. {status_text}",
        "This is {unit}. Wilco, executing now. {status_text}",
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

    template = random.choice(pool)

    # Fill in placeholders
    reasons = UNABLE_REASONS_RU if language == "ru" else UNABLE_REASONS_EN
    reason = reasons.get(reason_key, "") if reason_key else ""

    return template.format(
        unit=unit_name,
        reason=reason,
        status_text=status_text,
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

