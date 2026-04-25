# Plan: Тактические и игровые улучшения KShU
# Implementation Guide for Coding Agent

> **Читающий агент:** все ссылки на файлы — относительные пути от корня репозитория.
> Перед реализацией каждого пункта читай указанные файлы целиком — не угадывай сигнатуры функций.
> Каждый пункт содержит: мотивацию → точные изменения → граничные случаи → тест-критерий.

---

## Статус реализации

| # | Пункт | Приоритет | Сложность | Статус |
|---|---|---|---|---|
| 1 | Направление атаки (heading_deg) | Быстрая победа | Low | ⬜ |
| 2 | Дальность связи (comms range) | Быстрая победа | Low | ⬜ |
| 3 | Цикл ФО→Огонь (Observer→Fire) | Стратегический | High | ⬜ |
| 4 | Overwatch / Рубеж перекрытия | Стратегический | High | ⬜ |
| 5 | Топливо (Fuel system) | Быстрая победа | Low | ⬜ |
| 6 | Дезактивация РХБЗ | Перспектива | Low | ⬜ |
| 7 | ФРАГО | Средний | Medium | ⬜ |
| 8 | Каскадирование замысла | Стратегический | High | ⬜ |
| 9 | ВАРНО | Перспектива | Low | ⬜ |
| 10 | Контроль объектов / Захват рубежа | Средний | Medium | ⬜ |
| 11 | ААР / Replay | Средний | Medium | ⬜ |
| 12 | Инъекция трения | Средний | Low | ⬜ |
| 13 | Скриптованный Red AI | Перспектива | Medium | ⬜ |
| 14 | OPORD Builder | Перспектива | Low | ⬜ |
| 15 | LZ/PZ риск высадки | Перспектива | Medium | ⬜ |
| 16 | ПВО (Air Defense) | Стратегический | Medium | ⬜ |
| 17 | Адаптивное самообучение | Маховик качества | High | ⬜ |
| 18 | Tutorial / Onboarding (новые пункты ниже) | Критичный для UX | Medium | ⬜ |
| 19 | Cross-cutting corrections (см. §0) | Обязательно | — | ⬜ |

---

## 0. Cross-cutting Engineering Notes & Plan Corrections

> Эти правила применяются ко ВСЕМ пунктам ниже. Читать перед началом любой задачи. Они исправляют конкретные баги/недосказанности в исходных рецептах.

### 0.1 Geographic distance — единая утилита

В §1, §2, §4, §10, §15, §16 используется `(dy * 111320, dx * 74000)` для перевода градусов в метры. Это неверно для широт ≠ 48°. Создай **единую утилиту** и используй её везде:

**Файл:** `backend/engine/geo_utils.py` (новый):
```python
import math
from geoalchemy2.shape import to_shape

METERS_PER_DEG_LAT = 111_320.0

def meters_per_deg_lon(lat_deg: float) -> float:
    return METERS_PER_DEG_LAT * math.cos(math.radians(lat_deg))

def planar_offset_m(p_from, p_to) -> tuple[float, float, float]:
    """Returns (dx_east_m, dy_north_m, dist_m) using local tangent plane at p_from latitude."""
    dy_m = (p_to.y - p_from.y) * METERS_PER_DEG_LAT
    dx_m = (p_to.x - p_from.x) * meters_per_deg_lon(p_from.y)
    return dx_m, dy_m, math.hypot(dx_m, dy_m)

def bearing_deg(p_from, p_to) -> float:
    """Bearing from p_from to p_to in degrees (0=N, 90=E)."""
    dx, dy, _ = planar_offset_m(p_from, p_to)
    return math.degrees(math.atan2(dx, dy)) % 360
```

В §1 `_compute_flank_factor`, §2 `_find_nearest_relay_dist`, §4 `_process_overwatch`, §10 `_units_in_radius`, §15 `_check_lz_risk`, §16 `_process_air_defense` — **заменить локальные `dx*74000` на `planar_offset_m()`**. Константа `74_000` уже есть в `combat.py: METERS_PER_DEG_LON_AT_48` — оставить только как fallback в самом combat для совместимости.

### 0.2 Deterministic hash — точная сигнатура

В §3, §15, §16 встречается `_deterministic_hash(tick, unit_id, ...)` без определения. Используй существующую утилиту из `backend/engine/detection.py: _deterministic_roll(tick, *ids) -> float` (диапазон 0..1).

**Действие:** вынеси функцию в новый файл `backend/engine/_rng.py`:
```python
import hashlib

def deterministic_roll(tick: int, *ids) -> float:
    """Stable pseudo-random in [0,1) from tick + uuids. Use everywhere the
    engine needs reproducible randomness (replay must be deterministic)."""
    raw = f"{tick}:" + ":".join(str(i) for i in ids)
    h = hashlib.blake2b(raw.encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64
```

Заменить во всех местах:
- `_deterministic_hash(tick, *ids) % 100 / 100` → `deterministic_roll(tick, *ids)` (диапазон тот же 0..1)
- `_deterministic_hash(tick, *ids) % 360` → `deterministic_roll(tick, *ids) * 360`
- `_deterministic_hash(tick, *ids) % int(N)` → `deterministic_roll(tick, *ids) * N`

`detection.py: _deterministic_roll` оставить как обёртку: `_deterministic_roll = deterministic_roll`.

### 0.3 JSONB mutation — только через копию

SQLAlchemy не отслеживает изменения внутри JSONB-словаря. **Везде** где меняется `unit.capabilities`, `unit.current_task`, `MapObject.properties` — использовать паттерн:
```python
caps = dict(unit.capabilities or {})
caps["fuel"] = new_fuel
unit.capabilities = caps   # обязательное переприсваивание
```
Это уже частично сделано в текущем коде — **не нарушать**. Особенно в §3 (`marked_target`), §5 (`fuel`), §9 (`warno_state`), §12 (`friction`), §10 (`controlled_by`).

### 0.4 Event payload visibility

Все новые события (§1, §2, §3, §4, §5, §10, §11, §15, §16) обязаны иметь поле `visibility` (`"all"` | `"blue"` | `"red"` | `"admin"`). Если не указано в рецепте — установить по правилу:
- Боевые/обстрел/потери → `"all"`
- Внутренняя кухня (`target_designated`, `comms_change`, `fuel_depleted`) → `unit.side` (только своей стороне)
- Snapshot replay → `"admin"`

### 0.5 Heading update — критическая зависимость для §1

`#1 Direction-of-attack` зависит от `unit.heading_deg`. Сейчас `heading_deg` **обновляется только** в `movement.py` при движении. Стоящая в обороне единица сохранит старый heading с момента последнего движения. Исправить:

В `backend/engine/defense.py: process_defense()` — при `task.type == "defend"` и наличии `task.facing_deg` (опциональное поле) → `unit.heading_deg = task["facing_deg"]`. В phrasebook добавить распознавание «оборона фронтом на север», `facing: north/east/...`, и LLM prompt — поле `facing_deg` в parsed_order для defend-приказов.

### 0.6 Order pipeline integration checklist (для §3, §4, §6, §7, §9, §10)

Каждый новый `OrderType` обязан получить:
1. Запись в `OrderType` enum (`backend/schemas/order.py`).
2. Соответствующий `ResponseType` + bilingual templates в `backend/prompts/response_generator.py`.
3. Keyword-детектор + минимум 4 примера в `backend/data/order_phrasebook.toml` (по 2 EN + 2 RU).
4. Few-shot пример в `backend/prompts/order_parser.py`.
5. Маппинг в `backend/services/intent_interpreter.py: _ACTION_FROM_ORDER` (детерминированный).
6. Обработчик задачи в `tick.py: _process_orders()` или соответствующем engine-модуле.
7. Регрессионный тест в `backend/tests/test_order_pipeline.py`.

Невыполнение любого пункта → парсер не распознает приказ или оставит unit в idle.

### 0.7 Performance — O(N²) в §2, §4, §16

`_find_nearest_relay_dist`, `_process_overwatch`, `_process_air_defense` — наивные O(N²). При N>100 (учения с батальоном) — сотни мс на тик.

Решение: в начале тика построить `STRtree` из `shapely.strtree` для всех живых единиц по сторонам. Передавать эти индексы во все шаги, использующие пространственный поиск. Это уже есть в `pathfinding_service.py` — переиспользовать паттерн.

### 0.8 §11 Replay — оптимизация хранения

Snapshot каждого юнита каждый тик = ~150 байт × 50 юнитов × 1000 тиков = **7.5 MB на сессию** в таблице events, плюс это ломает индексы. Исправить:

1. Один Event на тик `event_type=tick_snapshot` с `payload={"units": [...]}` (один INSERT).
2. Sampling: каждые `SNAPSHOT_INTERVAL_TICKS = 5` (настраиваемо в session settings). Между снапшотами фронтенд интерполирует позиции линейно.
3. Снэпшотить только дельты (изменившиеся юниты) — поле `payload.delta_only=true`.
4. Индекс: `CREATE INDEX ix_events_replay ON events (session_id, tick) WHERE event_type='tick_snapshot'` (partial index).

### 0.9 §17 Learning — критическое уточнение

Нормализация `_normalize()` в §17c теряет язык: «move to» и «двигайся к» после `[LOC]/[N]` всё ещё разные строки — **и это правильно**. Но `Counter(e["order_type"])` не должен агрегировать русские и английские записи в одну группу. Добавить в `groups[key]`: `key = (language, normalized_text)`.

### 0.10 Hot-reload phrasebook (§17)

`reload_phrasebook()` упомянута, но реализация не описана. Детали:

В `backend/services/order_phrasebook.py`:
```python
_PHRASEBOOK_CACHE: dict | None = None
_PHRASEBOOK_MTIME: float = 0.0

def get_phrasebook() -> dict:
    global _PHRASEBOOK_CACHE, _PHRASEBOOK_MTIME
    mtime = PHRASEBOOK_PATH.stat().st_mtime
    if _PHRASEBOOK_CACHE is None or mtime != _PHRASEBOOK_MTIME:
        _PHRASEBOOK_CACHE = _load_toml()
        _PHRASEBOOK_MTIME = mtime
    return _PHRASEBOOK_CACHE

def reload_phrasebook():
    global _PHRASEBOOK_CACHE
    _PHRASEBOOK_CACHE = None
```

Все вызовы в `order_parser.py` должны идти через `get_phrasebook()`, а не глобальную константу импорта.

---

## I. Боевые механики (глубина)

---

### #1 — Направление атаки в бою (`heading_deg` → урон)

**Файл:** `backend/engine/combat.py`

**Мотивация:** `unit.heading_deg` хранится и обновляется `movement.py`, но не используется в расчёте урона. Атака с фланга/тыла должна давать тактическое преимущество.

**Реализация:**

Прочитай `combat.py` и найди точку где вычисляется `target_protection` (в `_resolve_direct_fire` или аналогичной функции). Добавь перед ней:

```python
FLANK_PROTECTION_REDUCTION = 0.75   # умножается на target_protection
REAR_PROTECTION_REDUCTION  = 0.55   # атака с тыла

def _compute_flank_factor(attacker, target) -> float:
    """Returns multiplier for target_protection. < 1.0 = flanking advantage."""
    if attacker.heading_deg is None or target.heading_deg is None:
        return 1.0
    if attacker.position is None or target.position is None:
        return 1.0
    from backend.engine.geo_utils import bearing_deg
    att_pt = to_shape(attacker.position)
    tgt_pt = to_shape(target.position)
    attack_bearing = bearing_deg(att_pt, tgt_pt)
    angle_diff = abs((attack_bearing - target.heading_deg + 360) % 360)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    if angle_diff > 120:
        return REAR_PROTECTION_REDUCTION
    elif angle_diff > 60:
        return FLANK_PROTECTION_REDUCTION
    return 1.0
```

В расчёте урона добавь:
```python
flank_factor = _compute_flank_factor(attacker_unit, target_unit)
damage = fire_effectiveness * DAMAGE_SCALAR / (target_protection * flank_factor)
```

Добавь в event `payload`: `"flank_factor": flank_factor` если `flank_factor < 1.0`.

**НЕ применять фланговый фактор:**
- `_process_area_fire` — непрямой огонь не зависит от направления
- Авиационные удары (`AVIATION_UNIT_TYPES`)
- Когда оба `heading_deg is None` — возвращать 1.0

**Тест-критерий (`backend/tests/test_combat.py`):**
- Фронтальная атака (0°) → flank_factor = 1.0
- Фланг (90°) → flank_factor = 0.75
- Тыл (180°) → flank_factor = 0.55
- `heading_deg = None` у любого → flank_factor = 1.0

**Доп. примечания (после правок §0):**
- Использовать `geo_utils.bearing_deg()` вместо локального вычисления.
- В docstring `combat.py` уже упоминается `flank_factor` (строка 6) — формула там есть, но реализации нет. Это ожидаемое состояние, реализовать сейчас.
- Heading у defending unit может быть устаревшим — см. §0.5.
- Для `_process_area_fire` (артиллерия по площадям) flank_factor НЕ применять — там нет «направления атаки».
- Для **ближнего боя** (`dist_m < 100`) flank_factor можно ослабить (например `1.0 - (1.0 - flank_factor) * 0.5`) — ближний бой круговой. Опционально.

---

### #2 — Дальность связи (Comms Distance Degradation)

**Файл:** `backend/engine/comms.py`

**Мотивация:** `comms.py` деградирует связь только от подавления. Без HQ-ретранслятора за 8+ км связь деградирует — стандартная военная реальность.

**Реализация:**

Добавь константы в начало `comms.py`:
```python
import math
from geoalchemy2.shape import to_shape

COMMS_RANGE_DEFAULT_M = 8_000.0
COMMS_RANGE_HQ_M      = 25_000.0   # штаб — ретранслятор с бо́льшим диапазоном
COMMS_RANGE_LONG_M    = 15_000.0   # арт., разведка, авиация

RELAY_UNIT_TYPES = {"headquarters", "command_post"}
LONG_RANGE_UNIT_TYPES = {
    "artillery_battery", "artillery_platoon",
    "recon_team", "recon_section",
    "attack_helicopter", "transport_helicopter", "recon_uav",
}
```

Добавь вспомогательную функцию:
```python
def _find_nearest_relay_dist(unit, all_units: list) -> float:
    if unit.position is None:
        return float('inf')
    from backend.engine.geo_utils import planar_offset_m
    u_pt = to_shape(unit.position)
    best = float('inf')
    for other in all_units:
        if other.is_destroyed or other.id == unit.id or other.side != unit.side:
            continue
        is_relay = (other.id == unit.parent_unit_id) or (other.unit_type in RELAY_UNIT_TYPES)
        if not is_relay or other.position is None:
            continue
        o_pt = to_shape(other.position)
        _, _, dist = planar_offset_m(u_pt, o_pt)
        if dist < best:
            best = dist
    return best
```

В основном цикле `process_comms()` ПОСЛЕ проверки подавления — добавить блок дистанционной деградации:
```python
# Relay-type units are never range-limited themselves
if unit.unit_type not in RELAY_UNIT_TYPES:
    unit_range = COMMS_RANGE_LONG_M if unit.unit_type in LONG_RANGE_UNIT_TYPES else COMMS_RANGE_DEFAULT_M
    relay_dist = _find_nearest_relay_dist(unit, all_units)
    too_far = relay_dist > unit_range

    if too_far and current_val == "operational":
        unit.comms_status = "degraded"
        events.append({
            "event_type": "comms_change",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} comms degraded — out of relay range ({relay_dist/1000:.1f} km)",
            "payload": {"from": "operational", "to": "degraded", "reason": "range"},
        })
    elif not too_far and current_val == "degraded" and suppression <= 0.3:
        unit.comms_status = "operational"
        events.append({
            "event_type": "comms_change",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} comms restored — relay in range",
            "payload": {"from": "degraded", "to": "operational", "reason": "range_restored"},
        })
```

**Граничные случаи:**
- `RELAY_UNIT_TYPES` (HQ, CP) — сами никогда не теряют связь по дистанции
- `parent_unit_id = None` и нет HQ рядом → деградация
- Процентная логика: сначала проверяем подавление (существующий код), потом дистанцию (новый код)

**Тест-критерий:**
- Пехота в 5 км от HQ → operational
- Пехота в 12 км без HQ → degraded
- HQ сам никогда не деградирует по дистанции

---

### #3 — Цикл ФО→Огонь (Fire Observer → Fire Mission Loop)

**Файлы:** `backend/schemas/order.py`, `backend/engine/combat.py`, `backend/engine/tick.py`, `backend/services/order_parser.py`, `backend/prompts/order_parser.py`, `backend/data/order_phrasebook.toml`

**Мотивация:** Сейчас артиллерия сама ищет цели. Реальный C2: ОП засекает → передаёт целеуказание → арт. стреляет → ОП корректирует.

**Шаг 1: Новые типы приказов в `backend/schemas/order.py`**

В `OrderType`:
```python
designate_target = "designate_target"   # ОП обозначает цель для артиллерии
adjust_fire      = "adjust_fire"        # корректировка по результату залпа
```

В `ResponseType`:
```python
wilco_designate = "wilco_designate"   # "Цель обозначена, передаю данные"
wilco_adjust    = "wilco_adjust"      # "Принял корректировку, применяю"
```

**Шаг 2: `marked_target` в `unit.capabilities` (JSONB)**

Структура (хранится в JSONB, не меняет схему БД):
```json
{
  "marked_target": {
    "target_unit_id": "UUID или null",
    "target_lat": 48.12,
    "target_lon": 24.56,
    "observer_unit_id": "UUID",
    "marked_at_tick": 15,
    "accuracy_m": 50,
    "stale_after_tick": 20
  }
}
```

**Шаг 3: Функция в `backend/engine/combat.py`**

Добавить `process_target_designation()`, вызывать из `tick.py` ПЕРЕД `process_artillery_support()`:

```python
DESIGNATION_STALE_TICKS = 5   # marked_target устаревает через 5 тиков

OBSERVER_UNIT_TYPES = {"observation_post", "recon_team", "recon_section", "engineer_recon_team"}

def process_target_designation(all_units: list, tick: int) -> list[dict]:
    events = []
    for unit in all_units:
        if unit.is_destroyed:
            continue
        task = unit.current_task or {}
        caps = dict(unit.capabilities or {})

        # Expire stale marked_target
        mt = caps.get("marked_target")
        if mt and tick > mt.get("stale_after_tick", 0):
            caps.pop("marked_target", None)
            unit.capabilities = caps
            continue

        if task.get("type") != "designate_target":
            continue

        target_unit_id = task.get("target_unit_id")
        target_lat     = task.get("target_lat")
        target_lon     = task.get("target_lon")
        if not target_unit_id and not (target_lat and target_lon):
            continue

        caps["marked_target"] = {
            "target_unit_id": str(target_unit_id) if target_unit_id else None,
            "target_lat": target_lat,
            "target_lon": target_lon,
            "observer_unit_id": str(unit.id),
            "marked_at_tick": tick,
            "accuracy_m": 30 if unit.unit_type == "observation_post" else 80,
            "stale_after_tick": tick + DESIGNATION_STALE_TICKS,
        }
        unit.capabilities = caps
        events.append({
            "event_type": "target_designated",
            "actor_unit_id": unit.id,
            "payload": caps["marked_target"],
            "text_summary": f"{unit.name} designated target for fire mission",
            "visibility": unit.side.value if hasattr(unit.side, 'value') else unit.side,
        })
    return events
```

**Шаг 4: Изменить `process_artillery_support()`**

В `combat.py`, в начале функции перед автопоиском добавить:
```python
# Priority: use observer-designated targets first
observer_targets = []
for u in all_units:
    if u.is_destroyed or u.side != artillery_unit.side:
        continue
    mt = (u.capabilities or {}).get("marked_target")
    if mt:
        observer_targets.append(mt)

if observer_targets:
    # Sort: earliest marked, then most accurate
    observer_targets.sort(key=lambda mt: (mt.get("marked_at_tick", 0), mt.get("accuracy_m", 999)))
    mt = observer_targets[0]
    target_lat = mt["target_lat"]
    target_lon = mt["target_lon"]
    # LOS check — observer must actually see (or have recent contact on) the target
    # before recording marked_target; this branch trusts upstream validation.
    accuracy_m = float(mt.get("accuracy_m", 80))
    from backend.engine._rng import deterministic_roll
    from backend.engine.geo_utils import METERS_PER_DEG_LAT, meters_per_deg_lon
    jitter_angle = deterministic_roll(tick, artillery_unit.id) * 360.0
    jitter_dist_m = deterministic_roll(tick + 1, artillery_unit.id) * accuracy_m
    target_lat += math.cos(math.radians(jitter_angle)) * jitter_dist_m / METERS_PER_DEG_LAT
    target_lon += math.sin(math.radians(jitter_angle)) * jitter_dist_m / meters_per_deg_lon(target_lat)
    # proceed with area fire at (target_lat, target_lon)
```

**Шаг 5: `adjust_fire` обработка**

В `tick.py`, при обнаружении задачи `adjust_fire` у единицы:
```python
elif task_type == "adjust_fire":
    delta_lat = task.get("delta_lat", 0)
    delta_lon = task.get("delta_lon", 0)
    # Find the observer_unit that has marked_target for this unit
    for obs_unit in all_units:
        caps = dict(obs_unit.capabilities or {})
        mt = caps.get("marked_target")
        if mt and mt.get("observer_unit_id") == str(unit.id):
            mt["target_lat"] = mt.get("target_lat", 0) + delta_lat
            mt["target_lon"] = mt.get("target_lon", 0) + delta_lon
            mt["accuracy_m"] = max(20, mt.get("accuracy_m", 80) * 0.6)  # улучшается точность
            caps["marked_target"] = mt
            obs_unit.capabilities = caps
    order.status = "completed"
```

**Шаг 6: Phrasebook (`backend/data/order_phrasebook.toml`)**

Добавить в конец файла:
```toml
[[case]]
input = "Observe and designate targets at B4-3"
order_type = "designate_target"
language = "en"

[[case]]
input = "Обозначь цель для миномётов, квадрат C7-5"
order_type = "designate_target"
language = "ru"

[[case]]
input = "Correct fire, shift 200m north"
order_type = "adjust_fire"
language = "en"

[[case]]
input = "Корректировка — перенос огня 150 метров западнее"
order_type = "adjust_fire"
language = "ru"
```

**Добавить в `backend/prompts/response_generator.py`** шаблоны для `wilco_designate` и `wilco_adjust`.

**Граничные случаи:**
- Артиллерия без `marked_target` работает как прежде (автопоиск)
- Несколько ОП обозначают разные цели → приоритет по времени (`marked_at_tick`)
- `adjust_fire` без активного `marked_target` → игнорировать, логировать

**Тест-критерий:**
- ОП отдаёт `designate_target` → через 1 тик: `unit.capabilities.marked_target` заполнен
- Артиллерия атакует обозначенную позицию (не автопоиск)
- Через `DESIGNATION_STALE_TICKS` тиков без обновления → `marked_target` удалён

**Доп. правки (после §0):**
- `observer_targets[0]` нестабилен — отсортировать по `marked_at_tick ASC, accuracy_m ASC`.
- Jitter координат: использовать `geo_utils.meters_per_deg_lon(target_lat)` для долготы, **не делить на 111320 для lon**. Корректная формула:
  ```python
  jitter_dist_m = _deterministic_roll(tick + 1, artillery_unit.id) * accuracy_m
  jitter_angle  = _deterministic_roll(tick, artillery_unit.id) * 360
  target_lat += math.cos(math.radians(jitter_angle)) * jitter_dist_m / METERS_PER_DEG_LAT
  target_lon += math.sin(math.radians(jitter_angle)) * jitter_dist_m / meters_per_deg_lon(target_lat)
  ```
- ОП должен иметь LOS до цели (или прямой контакт в `Contact`-таблице) — проверка `los_service.has_los(observer, target_pt)` перед записью `marked_target`. Иначе ОП «видит сквозь стены».
- `marked_target` сериализовать в `unit.capabilities.serializable` (str для UUID) — JSONB не принимает `uuid.UUID` напрямую.
- `adjust_fire` без `marked_target` → emit event `adjust_fire_no_target` для отладки тренера.
- В `visibility_service._serialize_unit()` пробрасывать `marked_target` только своей стороне (admin/own side), иначе противник увидит, что его обозначили.

---

### #4 — Overwatch / Рубеж перекрытия огнём

**Файлы:** `backend/schemas/order.py`, `backend/engine/tick.py`, `backend/data/order_phrasebook.toml`, `backend/prompts/order_parser.py`

**Мотивация:** Тактика «прыжком» — одна подгруппа прикрывает огнём, другая перемещается. Невозможна без задачи «стой и огонь при обнаружении в секторе».

**Шаг 1:** В `OrderType` добавить `overwatch = "overwatch"`. В `ResponseType` добавить `wilco_overwatch = "wilco_overwatch"`.

**Шаг 2:** Задача хранится в `unit.current_task`:
```json
{
  "type": "overwatch",
  "sector_bearing": 45.0,
  "sector_width_deg": 60.0,
  "covering_unit_id": "UUID-optional",
  "max_range_m": 800.0,
  "auto_engage": true
}
```

**Шаг 3:** В `tick.py` добавить шаг `1b. Process overwatch triggers` — ПОСЛЕ шага 1 (обработка приказов), ПЕРЕД шагом 2 (движение):

```python
def _process_overwatch(all_units: list, contacts_by_side: dict, tick: int) -> list[dict]:
    events = []
    for unit in all_units:
        if unit.is_destroyed or unit.position is None:
            continue
        task = unit.current_task or {}
        if task.get("type") != "overwatch":
            continue
        if not task.get("auto_engage", True):
            continue

        sector_bearing = float(task.get("sector_bearing", 0))
        sector_half    = float(task.get("sector_width_deg", 60)) / 2
        max_range_m    = float(task.get("max_range_m", 800))
        u_pt = to_shape(unit.position)

        for contact in contacts_by_side.get(unit.side, []):
            if contact.is_stale or contact.location_estimate is None:
                continue
            from backend.engine.geo_utils import planar_offset_m
            c_pt = to_shape(contact.location_estimate)
            dx_m, dy_m, dist_m = planar_offset_m(u_pt, c_pt)
            if dist_m > max_range_m:
                continue
            bearing_to = math.degrees(math.atan2(dx_m, dy_m)) % 360
            angle_diff = abs((bearing_to - sector_bearing + 360) % 360)
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            if angle_diff <= sector_half:
                # Save overwatch task for restoration after fire
                new_task = {
                    "type": "fire",
                    "target_unit_id": str(contact.target_unit_id) if contact.target_unit_id else None,
                    "target_lat": c_pt.y,
                    "target_lon": c_pt.x,
                    "auto_overwatch": True,
                    "_overwatch_restore": dict(task),
                }
                unit.current_task = new_task
                events.append({
                    "event_type": "overwatch_engaged",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} engaged enemy in overwatch sector ({bearing_to:.0f}°)",
                    "payload": {"bearing": bearing_to, "dist_m": dist_m},
                    "visibility": unit.side.value if hasattr(unit.side, 'value') else unit.side,
                })
                break  # одна цель за тик

        # Restore overwatch task when no enemy in sector anymore
        current_task = unit.current_task or {}
        if current_task.get("auto_overwatch"):
            # Check if target still valid
            target_id = current_task.get("target_unit_id")
            still_valid = any(
                str(c.target_unit_id) == target_id
                for c in contacts_by_side.get(unit.side, [])
                if not c.is_stale
            )
            if not still_valid:
                unit.current_task = current_task.get("_overwatch_restore", {"type": "overwatch", **task})
    return events
```

**Шаг 4:** Phrasebook:
```toml
[[case]]
input = "Hold position and cover Alpha squad's advance on the left"
order_type = "overwatch"
language = "en"

[[case]]
input = "Прикрой выдвижение второго взвода, сектор север"
order_type = "overwatch"
language = "ru"
```

LLM prompt: `"overwatch" — unit holds and auto-fires at enemies entering a designated sector. Fields: sector_bearing (degrees from north), sector_width_deg (default 60), max_range_m (default 800).`

**Граничные случаи:**
- `sector_bearing` не указан явно → LLM подставляет текущий `unit.heading_deg`
- Overwatch НЕ снимается автоматически когда покрываемая единица завершила движение
- Overwatch единица не двигается (`type=overwatch` ≠ `type=move`)

**Тест-критерий:**
- Единица с overwatch + враг в секторе → событие `overwatch_engaged` в тот же тик, задача меняется на fire
- Враг вне сектора → overwatch сохраняется, нет огня
- После исчезновения контакта → задача восстанавливается в `overwatch`

**Доп. правки (после §0):**
- `contacts_by_side` строится один раз в начале тика как `dict[str, list[Contact]]` и передаётся в `_process_overwatch`, `_process_artillery_support`, `radio_chatter`. Не дёргать БД из цикла.
- Вместо отдельного `_overwatch_restore` хранить параметры сектора **на уровне `unit.capabilities.overwatch_config`** (постоянная директива), а `current_task` уже становится временным `fire`. Это устраняет проблему «restore stale snapshot».
  ```json
  unit.capabilities.overwatch_config = {"sector_bearing": 45, "sector_width_deg": 60, "max_range_m": 800}
  ```
  Восстановление: если `unit.current_task.auto_overwatch` и цель ушла → `unit.current_task = {"type":"overwatch", **caps["overwatch_config"]}`.
- `auto_overwatch=True` единица **не двигается в режиме fire** — добавить guard в `movement.py: process_movement()`: `if task.get("auto_overwatch"): skip movement`. Иначе она побежит на цель.
- Сектор по умолчанию: `unit.heading_deg` если LLM не указал.
- Огонь из overwatch расходует ammo как обычный fire — учесть в §5/§7 «warno: ammo bonus».

---

### #5 — Топливо (Fuel System)

**Файлы:** `backend/engine/movement.py`, `backend/engine/resupply.py`, `backend/services/visibility_service.py`, `frontend/js/units.js`

**Мотивация:** Бронетехника имеет ограниченный запас хода. `fuel` — аналог `ammo`, хранится в JSONB `unit.capabilities`, не требует изменений схемы БД.

**Шаг 1: Константы в `backend/engine/movement.py`**

```python
FUEL_CONSUMING_UNIT_TYPES = {
    "tank_platoon", "tank_company",
    "mech_platoon", "mech_company",
    "artillery_battery", "artillery_platoon",
    "avlb_vehicle", "avlb_section",
    "logistics_platoon", "logistics_section",
    "headquarters", "command_post",
    "attack_helicopter", "transport_helicopter",
}

FUEL_CONSUMPTION_RATE_GROUND   = 0.002   # за тик движения (~500 тиков до пустого бака)
FUEL_CONSUMPTION_RATE_AVIATION = 0.006   # вертолёты расходуют быстрее
FUEL_LOW_THRESHOLD  = 0.20              # ниже этого — скорость как пехота
FUEL_EMPTY_THRESHOLD = 0.02             # стоп
FUEL_SLOW_SPEED_MPS  = 1.2             # m/s при низком топливе
```

**Шаг 2:** В `process_movement()`, для каждой движущейся единицы (добавить ПЕРЕД применением `effective_speed`):

```python
if unit.unit_type in FUEL_CONSUMING_UNIT_TYPES:
    caps = dict(unit.capabilities or {})
    fuel = float(caps.get("fuel", 1.0))

    if fuel <= FUEL_EMPTY_THRESHOLD:
        if not caps.get("_fuel_empty_reported"):
            caps["_fuel_empty_reported"] = True
            unit.capabilities = caps
            events.append({
                "event_type": "fuel_depleted",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} halted — fuel depleted",
                "payload": {"fuel": fuel},
            })
        continue  # skip movement entirely

    if fuel < FUEL_LOW_THRESHOLD:
        effective_speed = min(effective_speed, FUEL_SLOW_SPEED_MPS)

    rate = FUEL_CONSUMPTION_RATE_AVIATION if unit.unit_type in AVIATION_UNIT_TYPES else FUEL_CONSUMPTION_RATE_GROUND
    fuel = max(0.0, fuel - rate)
    caps["fuel"] = fuel
    caps.pop("_fuel_empty_reported", None)
    unit.capabilities = caps
```

`AVIATION_UNIT_TYPES` уже определён в `movement.py` — проверь и используй существующую константу.

**Шаг 3: Восполнение в `backend/engine/resupply.py`**

Добавить константы:
```python
SUPPLY_CACHE_FUEL_RATE   = 0.08
LOGISTICS_UNIT_FUEL_RATE = 0.06
```

В `process_resupply()`, рядом с блоком `ammo` добавить аналогичный блок для `fuel`:
```python
from backend.engine.movement import FUEL_CONSUMING_UNIT_TYPES
if unit.unit_type in FUEL_CONSUMING_UNIT_TYPES:
    caps = dict(unit.capabilities or {})
    fuel = float(caps.get("fuel", 1.0))
    if fuel < 1.0:
        rate = SUPPLY_CACHE_FUEL_RATE  # or LOGISTICS_UNIT_FUEL_RATE depending on source
        caps["fuel"] = min(1.0, fuel + rate)
        unit.capabilities = caps
```

**Шаг 4:** В `backend/services/visibility_service.py`, в `_serialize_unit()` добавить:
```python
"fuel": float((unit.capabilities or {}).get("fuel", 1.0)) if unit.unit_type in FUEL_CONSUMING_UNIT_TYPES else None,
```

**Шаг 5:** В `frontend/js/units.js`, в tooltip/карточку единицы — добавить `⛽ ${Math.round(data.fuel*100)}%` для единиц, где `data.fuel !== null`. Предупреждение (оранжевый) при `fuel < 0.20`.

**Инициализация:** В `backend/services/session_service.py`, при создании единиц из `initial_units` — добавить `"fuel": 1.0` в `capabilities` для типов из `FUEL_CONSUMING_UNIT_TYPES`.

**Граничные случаи:**
- Пехота, разведка, инженерные пешие → `fuel` не используется и не отображается (`fuel = None`)
- Авиация в статике (не движется) → топливо НЕ расходуется (только при `process_movement()`)
- Задача `resupply` → добавить `fuel` как вторую цель наравне с `ammo`

**Тест-критерий:**
- Танк движется 500 тиков → `fuel ≈ 0` → событие `fuel_depleted`, остановлен
- Логист. единица в 50 м → `fuel` растёт +0.06/тик
- Пехота: `fuel = None`, движется без ограничений

---

### #6 — Дезактивация РХБЗ (Decontamination)

**Файлы:** `backend/engine/engineering.py`, `backend/schemas/order.py`, `backend/data/order_phrasebook.toml`

**Шаг 1:** В `OrderType` добавить `decontaminate = "decontaminate"`.

**Шаг 2:** В `engineering.py` добавить в блоке обработки задач (`task_type == ...`):

```python
DECONTAMINATE_TICKS = 5
DECON_UNIT_TYPES = {
    "combat_engineer_platoon", "combat_engineer_section",
    "combat_engineer_team", "engineer_platoon", "engineer_section",
}

elif task_type == "decontaminate":
    if unit.unit_type not in DECON_UNIT_TYPES:
        continue
    target_obj_id = task.get("target_object_id")
    target_obj = next((o for o in map_objects if str(o.id) == target_obj_id), None)
    if not target_obj or target_obj.object_type != "chemical_cloud":
        continue
    props = dict(target_obj.properties or {})
    progress = int(props.get("decon_progress", 0)) + 1
    if progress >= DECONTAMINATE_TICKS:
        # Remove chemical cloud
        map_objects_to_delete.append(target_obj.id)
        events.append({
            "event_type": "decontamination_complete",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} completed decontamination",
        })
        unit.current_task = None
    else:
        props["decon_progress"] = progress
        target_obj.properties = props
```

**Phrasebook:**
```toml
[[case]]
input = "Decontaminate the area at C4-2"
order_type = "decontaminate"
language = "en"

[[case]]
input = "Провести дезактивацию района B7-3"
order_type = "decontaminate"
language = "ru"
```

---

## II. Командный цикл (C2)

---

### #7 — ФРАГО (Fragmentary Order)

**Файлы:** `backend/schemas/order.py`, `backend/services/order_parser.py`, `backend/engine/tick.py`, `backend/data/order_phrasebook.toml`

**Мотивация:** Изменить задачу «на лету» без полной отмены и переиздания — стандартная боевая практика.

**Шаг 1:** В `ParsedOrderData` (found in `backend/schemas/order.py`) добавить поля:
```python
is_frago: bool = False
frago_patch: dict | None = None   # только изменённые поля задачи
```

**Шаг 2: Keyword markers в `backend/services/order_parser.py`**

Добавить в keyword detection (до LLM вызова):
```python
FRAGO_KEYWORDS_EN = {"change target", "new objective", "shift to", "instead go to", "redirect to"}
FRAGO_KEYWORDS_RU = {"скорректируй", "смени направление", "смени цель", "вместо", "новый рубеж", "поправка к приказу"}

def _is_frago(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in FRAGO_KEYWORDS_EN | FRAGO_KEYWORDS_RU)
```

Если `_is_frago()` → передать LLM hint: `"This is a FRAGO — extract only the CHANGED fields"` и установить `is_frago=True` в результате.

**Шаг 3:** В `tick.py`, в `_process_orders()`, при обработке приказа если `parsed_order.get("is_frago")`:

```python
if parsed_order.get("is_frago") and parsed_order.get("frago_patch"):
    patch = parsed_order["frago_patch"]
    current_task = dict(unit.current_task or {})
    # Apply only the changed fields
    for key, val in patch.items():
        if val is not None:
            current_task[key] = val
    current_task["_recalc_waypoints"] = True   # trigger A* recalc next tick
    unit.current_task = current_task
    order.status = OrderStatus.completed         # ФРАГО применяется мгновенно
    continue   # don't create new task from scratch
```

**Шаг 4:** Phrasebook:
```toml
[[case]]
input = "Change target — shift to grid F8-3-1 instead"
order_type = "move"
is_frago = true
language = "en"

[[case]]
input = "Поправка к приказу — новый рубеж C9-2, не A7"
order_type = "move"
is_frago = true
language = "ru"
```

**Граничные случаи:**
- Нет активной задачи → ФРАГО становится обычным приказом
- Patch меняет только `speed` → `target_*` остаётся прежним, waypoints НЕ пересчитываются
- ФРАГО НЕ сбрасывает `combat_role` (suppress/flank/assault)

---

### #8 — Каскадирование замысла (Intent Cascade)

**Файлы:** Новый файл `backend/engine/intent_cascade.py`, `backend/engine/tick.py`, `backend/api/units.py` (для HQ_TYPES)

**Мотивация:** HQ-рота получает `attack` → взводы-подчинённые автоматически нарезают роли без ручного ввода.

**Новый файл `backend/engine/intent_cascade.py`:**

```python
"""
Intent Cascade — deterministic (no LLM) sub-task assignment for subordinates.
Called when an HQ unit receives an order.
"""

CASCADE_TASK_TYPES = {"attack", "defend", "move", "withdraw", "disengage"}

# Unit type prefixes considered HQ-capable (имеют подчинённых)
HQ_UNIT_TYPES = {"headquarters", "command_post", "infantry_company", "infantry_battalion",
                 "tank_company", "mech_company", "artillery_battery"}


def should_cascade(unit, task: dict) -> bool:
    return (
        task.get("type") in CASCADE_TASK_TYPES
        and unit.unit_type in HQ_UNIT_TYPES
    )


def cascade_intent(parent_unit, parent_task: dict, all_units: list) -> list[dict]:
    """
    Returns list of {unit_id, task} assignments for idle subordinates.
    Does NOT mutate units — caller applies assignments.
    """
    task_type = parent_task.get("type")
    subordinates = [
        u for u in all_units
        if u.parent_unit_id == parent_unit.id
        and not u.is_destroyed
        and not (u.current_task or {}).get("type")   # only idle subs
    ]
    if not subordinates:
        return []

    if task_type == "attack":
        return _cascade_attack(parent_task, subordinates)
    elif task_type == "defend":
        return _cascade_defend(parent_task, subordinates)
    elif task_type in ("withdraw", "disengage"):
        return _cascade_withdraw(parent_task, subordinates)
    elif task_type == "move":
        return _cascade_move(parent_task, subordinates)
    return []


def _cascade_attack(task, subs):
    subs_sorted = sorted(subs, key=lambda u: u.strength or 0, reverse=True)
    assigned = []
    for i, sub in enumerate(subs_sorted):
        if i == 0:
            role, sub_type = "assault", "attack"
        elif i == 1:
            role, sub_type = "suppress", "fire"
        else:
            role, sub_type = "flank", "attack"
        assigned.append({
            "unit_id": sub.id,
            "task": {**task, "type": sub_type, "combat_role": role, "_cascaded": True},
        })
    return assigned


def _cascade_defend(task, subs):
    subs_sorted = sorted(subs, key=lambda u: u.strength or 0, reverse=True)
    assigned = []
    roles = ["main_defense", "reserve", "observation"]
    for i, sub in enumerate(subs_sorted):
        role = roles[min(i, len(roles)-1)]
        sub_task = {**task, "type": "defend", "sub_role": role, "_cascaded": True}
        if role == "reserve":
            sub_task["type"] = "observe"   # reserve waits
        assigned.append({"unit_id": sub.id, "task": sub_task})
    return assigned


def _cascade_withdraw(task, subs):
    # All withdraw, fastest units lead
    return [{"unit_id": sub.id, "task": {**task, "_cascaded": True}} for sub in subs]


def _cascade_move(task, subs):
    # Column: all follow in sequence
    return [
        {"unit_id": sub.id, "task": {**task, "type": "move", "follow_leader_id": str(subs[i-1].id) if i > 0 else None, "_cascaded": True}}
        for i, sub in enumerate(subs)
    ]
```

**В `tick.py`** — после `_process_orders()` добавить:

```python
from backend.engine.intent_cascade import should_cascade, cascade_intent

async def _process_intent_cascade(all_units, units_by_id):
    events = []
    for unit in all_units:
        task = unit.current_task or {}
        if not task.get("_cascade_pending"):
            continue
        from backend.engine.intent_cascade import cascade_intent
        assignments = cascade_intent(unit, task, all_units)
        for a in assignments:
            sub = units_by_id.get(a["unit_id"])
            if sub:
                sub.current_task = a["task"]
        # Clear flag
        task_copy = dict(task)
        task_copy.pop("_cascade_pending", None)
        unit.current_task = task_copy
    return events
```

При назначении задачи HQ-единице в `_process_orders()` — добавлять `_cascade_pending: True` в task.

**Граничные случаи:**
- Только `idle_subs` (нет активной задачи) получают каскадную задачу
- Каскад НЕ перебивает активно воюющих подчинённых
- `_cascaded: True` метка — чтобы не каскадировать каскадную задачу повторно

---

### #9 — ВАРНО (Warning Order)

**Файлы:** `backend/schemas/order.py`, `backend/engine/tick.py`, `backend/data/order_phrasebook.toml`

**Шаг 1:** `OrderType.warno = "warno"`. `ResponseType.wilco_warno = "wilco_warno"`.

**Шаг 2:** В `tick.py`, обработка `warno` задачи:
```python
elif task_type == "warno":
    warned_for = task.get("warning_for")  # тип предстоящей задачи
    caps = dict(unit.capabilities or {})
    caps["warno_state"] = {"warned_for": warned_for, "since_tick": tick}
    unit.capabilities = caps
    # Ammo/morale readiness bonus
    unit.ammo = min(1.0, unit.ammo + 0.01)
    unit.morale = min(1.0, unit.morale + 0.01)
    order.status = OrderStatus.completed
```

Когда единица получает реальный приказ типа `warned_for` → удалять `warno_state`, применять бонус готовности `+0.05 morale`.

**Phrasebook:**
```toml
[[case]]
input = "Prepare for attack, standby for execute order"
order_type = "warno"
language = "en"

[[case]]
input = "Готовность к атаке — ждите приказа на выдвижение"
order_type = "warno"
language = "ru"
```

---

### #10 — Контроль объектов / Захват рубежа

**Файлы:** `backend/engine/map_objects.py`, `backend/engine/tick.py`, `backend/services/report_generator.py`, `frontend/js/map_objects.js`, `frontend/js/admin.js`

**Мотивация:** Заменить LLM-арбитр победы на детерминированный контроль точек.

**Шаг 1: Новые типы объектов в `MAP_OBJECT_DEFS` (`backend/engine/map_objects.py`)**

```python
"objective_point": {
    "category": "objective",
    "geometry_type": "Point",
    "control_radius_m": 100,
    "capture_ticks": 5,
    "effect_radius_m": 100,
    "description": "Tactical objective — capture by holding for N ticks",
    "color": "#FFD700",
},
"objective_area": {
    "category": "objective",
    "geometry_type": "Polygon",
    "capture_ticks": 8,
    "description": "Tactical objective area",
    "color": "#FFD700",
},
```

Properties JSONB для этих объектов:
```json
{
  "controlled_by": "neutral",
  "control_ticks_blue": 0,
  "control_ticks_red": 0,
  "capture_ticks_required": 5,
  "objective_label": "Высота 170",
  "objective_value": 1
}
```

**Шаг 2: `process_objective_control()` в `backend/engine/map_objects.py`**

```python
def process_objective_control(all_units: list, map_objects: list, tick: int) -> list[dict]:
    events = []
    OBJECTIVE_TYPES = {"objective_point", "objective_area"}

    for obj in map_objects:
        if obj.object_type not in OBJECTIVE_TYPES:
            continue
        props = dict(obj.properties or {})
        obj_geom = to_shape(obj.geometry)
        # Use centroid for polygons; Point geometries return themselves
        obj_center = obj_geom.centroid if obj_geom.geom_type != "Point" else obj_geom
        radius_m = MAP_OBJECT_DEFS[obj.object_type].get("control_radius_m", 100)
        capture_req = int(props.get("capture_ticks_required", 5))
        prev_controller = props.get("controlled_by", "neutral")

        blue_present = _units_in_radius(all_units, obj_center, radius_m, side="blue")
        red_present  = _units_in_radius(all_units, obj_center, radius_m, side="red")
        contested = blue_present and red_present

        if contested:
            props["control_ticks_blue"] = max(0, props.get("control_ticks_blue", 0) - 1)
            props["control_ticks_red"]  = max(0, props.get("control_ticks_red", 0) - 1)
        elif blue_present:
            props["control_ticks_blue"] = props.get("control_ticks_blue", 0) + 1
            props["control_ticks_red"]  = 0
        elif red_present:
            props["control_ticks_red"]  = props.get("control_ticks_red", 0) + 1
            props["control_ticks_blue"] = 0
        else:
            props["control_ticks_blue"] = max(0, props.get("control_ticks_blue", 0) - 1)
            props["control_ticks_red"]  = max(0, props.get("control_ticks_red", 0) - 1)

        new_controller = prev_controller
        if props.get("control_ticks_blue", 0) >= capture_req:
            new_controller = "blue"
        elif props.get("control_ticks_red", 0) >= capture_req:
            new_controller = "red"

        props["controlled_by"] = new_controller
        obj.properties = props

        if new_controller != prev_controller:
            events.append({
                "event_type": "objective_captured",
                "actor_unit_id": None,   # объект, не юнит
                "payload": {
                    "object_id": str(obj.id),
                    "label": props.get("objective_label", ""),
                    "captured_by": new_controller,
                    "lost_by": prev_controller,
                },
                "text_summary": f"Objective '{props.get('objective_label','')}' captured by {new_controller}",
                "visibility": "all",
            })
    return events


def _units_in_radius(units, center_pt, radius_m: float, side: str) -> bool:
    from backend.engine.geo_utils import planar_offset_m
    for unit in units:
        if unit.is_destroyed or (unit.side.value if hasattr(unit.side, 'value') else unit.side) != side:
            continue
        if unit.position is None:
            continue
        u_pt = to_shape(unit.position)
        _, _, dist = planar_offset_m(center_pt, u_pt)
        if dist <= radius_m:
            return True
    return False
```

> NB: для масштабных учений с >50 объектов и >100 юнитов заменить `_units_in_radius` на STRtree-индексированный поиск (см. §0.7).

**Шаг 3: Вызов в `tick.py`** — добавить после combat, перед events:
```python
objective_events = process_objective_control(all_units, all_map_objects, session.tick)
all_events.extend(objective_events)
```

**Шаг 4: Детерминированная проверка победы в `tick.py`**

```python
def _check_deterministic_victory(scenario_objectives: dict, map_objects: list) -> str | None:
    det = (scenario_objectives or {}).get("deterministic", {})
    if not det:
        return None
    OBJECTIVE_TYPES = {"objective_point", "objective_area"}
    blue_count = sum(
        1 for o in map_objects
        if o.object_type in OBJECTIVE_TYPES
        and (o.properties or {}).get("controlled_by") == "blue"
    )
    red_count = sum(
        1 for o in map_objects
        if o.object_type in OBJECTIVE_TYPES
        and (o.properties or {}).get("controlled_by") == "red"
    )
    if blue_count >= det.get("blue_needs", 9999):
        return "blue"
    if red_count >= det.get("red_needs", 9999):
        return "red"
    return None
```

**Шаг 5: Фронтенд**

- В `map_objects.js` — рендеринг objective с цветом по `controlled_by` (синий/красный/серый)
- Прогресс-бар: `control_ticks_blue / capture_ticks_required` для синих, аналогично для красных
- В `admin.js` — добавить `objective` в список типов объектов

**Шаг 6: SITREP** — в `report_generator.py` добавить статус objectives в SITREP-тело.

**Тест-критерий:**
- Синяя единица в 100 м объекта 5 тиков → `controlled_by = "blue"`, событие `objective_captured`
- Красная входит → contested, прогресс Stop
- Синяя уходит → красная захватывает через ещё 5 тиков

---

## III. UX для учений / Контроль тренировки

---

### #11 — ААР / Replay

**Файлы:** Новый `backend/api/replay.py`, новый `frontend/js/replay.js`, `backend/engine/tick.py`, `backend/engine/events.py`, новая alembic миграция для индекса

**Архитектура:** Таблица `Event` уже append-only. Для воспроизведения нужны позиции единиц по тикам — их нет в Unit (только текущее состояние). Решение: **один** snapshot-Event на тик с массивом всех юнитов в `payload.units` (см. §0.8 — экономия INSERT и индексов).

**Шаг 1: Snapshot в `backend/engine/tick.py`**

В конце тика, перед `db.flush()`:
```python
SNAPSHOT_INTERVAL_TICKS = 5   # настраиваемо в session.settings

if session.tick % SNAPSHOT_INTERVAL_TICKS == 0:
    snapshot_payload = _snapshot_unit_positions(all_units, session.tick, prev_snapshot)
    if snapshot_payload["units"]:   # пропустить если дельта пуста
        db.add(Event(
            session_id=session_id,
            tick=session.tick,
            game_timestamp=session.current_time,
            event_type="tick_snapshot",
            visibility="admin",
            actor_unit_id=None,
            payload=snapshot_payload,
            text_summary=None,
        ))
```

```python
def _snapshot_unit_positions(all_units, tick: int, prev_snapshot: dict | None = None) -> dict:
    """Returns single payload with all (or delta) units. Set delta_only=True
    when prev_snapshot is provided and only changed units are emitted."""
    prev_units = {u["unit_id"]: u for u in (prev_snapshot or {}).get("units", [])}
    units_data = []
    for unit in all_units:
        if unit.position is None:
            continue
        pt = to_shape(unit.position)
        side_val = unit.side.value if hasattr(unit.side, 'value') else unit.side
        cur = {
            "unit_id": str(unit.id),
            "lat": pt.y, "lon": pt.x,
            "side": side_val,
            "strength": unit.strength,
            "is_destroyed": unit.is_destroyed,
            "task_type": (unit.current_task or {}).get("type"),
            "heading_deg": unit.heading_deg,
        }
        # Delta filter: only emit if changed beyond noise threshold
        prev = prev_units.get(cur["unit_id"])
        if prev:
            same_pos    = abs(prev["lat"] - cur["lat"]) < 1e-5 and abs(prev["lon"] - cur["lon"]) < 1e-5
            same_state  = (prev.get("task_type") == cur["task_type"]
                           and prev.get("is_destroyed") == cur["is_destroyed"]
                           and abs((prev.get("strength") or 0) - (cur["strength"] or 0)) < 0.01)
            if same_pos and same_state:
                continue
        units_data.append(cur)
    return {"units": units_data, "delta_only": prev_snapshot is not None, "tick": tick}
```

**Replay reconstruction**: фронтенд держит «полный кадр» от последнего non-delta снапшота и применяет дельты поверх. Между снапшотами (5 тиков) — линейная интерполяция позиций (`requestAnimationFrame` lerp).

**Индекс** (см. §0.8): `CREATE INDEX ix_events_replay ON events (session_id, tick) WHERE event_type='tick_snapshot'` — partial index.

**Шаг 2: `backend/api/replay.py`**

```python
@router.get("/sessions/{session_id}/replay")
async def get_replay(
    session_id: uuid.UUID,
    from_tick: int = 0,
    to_tick: int = 9999,
    db: AsyncSession = Depends(get_db),
    participant = Depends(get_session_participant),
):
    if participant.role not in ("admin", "observer"):
        raise HTTPException(403, "Replay requires admin or observer role")

    result = await db.execute(
        select(Event)
        .where(
            Event.session_id == session_id,
            Event.event_type == "tick_snapshot",
            Event.tick >= from_tick,
            Event.tick <= to_tick,
        )
        .order_by(Event.tick)
    )
    events = result.scalars().all()

    # Each Event.payload = {"units": [...], "delta_only": bool, "tick": N}
    by_tick: dict[int, dict] = {ev.tick: ev.payload for ev in events}

    return {"ticks": by_tick, "from_tick": from_tick, "to_tick": to_tick,
            "snapshot_interval": 5}
```

Зарегистрировать в `backend/main.py`.

**Шаг 3: Alembic миграция** — partial index, только для snapshot-строк:
```python
op.execute("""
    CREATE INDEX IF NOT EXISTS ix_events_replay
    ON events (session_id, tick)
    WHERE event_type = 'tick_snapshot'
""")
```

**Шаг 4: `frontend/js/replay.js`**

```javascript
const KReplay = {
    _data: {},        // {tick: [{unit_id, lat, lon, side, strength, ...}]}
    _markerLayer: null,
    _trailLayers: {},
    _currentTick: 0,
    _maxTick: 0,
    _playing: false,
    _timer: null,

    async load(sessionId, fromTick = 0, toTick = 9999) {
        const res = await fetch(`/api/sessions/${sessionId}/replay?from_tick=${fromTick}&to_tick=${toTick}`);
        const data = await res.json();
        this._data = data.ticks;
        this._maxTick = Math.max(...Object.keys(data.ticks).map(Number));
        this._buildTrails();
    },

    render(tick) {
        const snapshots = this._data[tick] || [];
        // Clear and redraw unit markers at snapshot positions
        // Use KSymbols for milsymbol rendering
    },

    play(speed = 1) {
        this._playing = true;
        this._timer = setInterval(() => {
            if (this._currentTick >= this._maxTick) { this.pause(); return; }
            this.render(++this._currentTick);
            document.getElementById('replay-slider').value = this._currentTick;
        }, 1000 / speed);
    },

    pause()  { this._playing = false; clearInterval(this._timer); },

    _buildTrails() {
        // Build Leaflet Polylines: {unit_id → [[lat,lon], ...]} across all ticks
        // Color by side, fade opacity for older tick positions
    },
};
```

UI: В admin панели → Monitor → кнопка "📹 Replay". Открывает overlay со слайдером, кнопками ▶ ⏸ ◀ ▶▶. Треки движения — тонкие polyline поверх карты.

---

### #12 — Инъекция трения (Trainer Friction Tool)

**Файлы:** `backend/api/admin.py`, `backend/engine/tick.py`, `frontend/js/admin.js`

**Шаг 1:** Добавить в `backend/api/admin.py`:

```python
class FrictionRequest(BaseModel):
    unit_id: uuid.UUID
    friction_type: str   # "breakdown"|"comms_failure"|"position_error"|"ammo_shortage"|"fuel_depletion"|"commander_casualty"
    duration_ticks: int = 5
    magnitude: float = 1.0   # 0.0–1.0
    comment: str = ""

@router.post("/sessions/{session_id}/inject-friction")
async def inject_friction(session_id, body: FrictionRequest, db = Depends(get_db)):
    unit = await db.get(Unit, body.unit_id)
    if not unit or str(unit.session_id) != str(session_id):
        raise HTTPException(404)
    session = await db.get(Session, session_id)
    caps = dict(unit.capabilities or {})
    friction_list = caps.get("friction", [])
    friction_list.append({
        "type": body.friction_type,
        "until_tick": session.tick + body.duration_ticks,
        "magnitude": body.magnitude,
        "comment": body.comment,
    })
    caps["friction"] = friction_list
    unit.capabilities = caps
    await db.commit()
    return {"ok": True}
```

**Шаг 2:** В `backend/engine/tick.py`, в начале тика для каждой единицы вызывать `_apply_friction(unit, current_tick)`:

```python
def _apply_friction(unit, current_tick: int):
    caps = dict(unit.capabilities or {})
    friction_list = caps.get("friction", [])
    if not friction_list:
        return
    active, expired = [], []
    for f in friction_list:
        if current_tick > f["until_tick"]:
            expired.append(f)
        else:
            active.append(f)
            ftype = f["type"]
            mag   = float(f.get("magnitude", 1.0))
            if ftype == "breakdown":
                unit.move_speed_mps = 0.0
            elif ftype == "comms_failure":
                unit.comms_status = "offline"
            elif ftype == "ammo_shortage":
                unit.ammo = max(0.0, unit.ammo - mag * 0.05)
            elif ftype == "fuel_depletion":
                c = dict(unit.capabilities or {})
                c["fuel"] = max(0.0, float(c.get("fuel", 1.0)) - mag * 0.1)
                unit.capabilities = c
            elif ftype == "commander_casualty":
                unit.morale = max(0.0, unit.morale - mag * 0.05)
                if unit.comms_status == "operational":
                    unit.comms_status = "degraded"
            # position_error — редкий кейс, применять осторожно
    if expired:
        caps["friction"] = active
        unit.capabilities = caps
```

**Шаг 3: Фронтенд**

В `admin.js` → Unit Dashboard → рядом с каждой единицей кнопка ⚡ "Inject Friction". Диалог (использовать `KDialogs.select()` + `KDialogs.prompt()`): тип, продолжительность, сила. Confirmation перед отправкой.

---

### #13 — Скриптованный Red AI

**Файлы:** `backend/models/red_agent.py`, `backend/api/red_ai.py`, `backend/services/red_ai/runner.py`, `frontend/js/admin.js`

**Шаг 1:** В `RedAgent.doctrine_profile` JSONB — поддержать ключ `script_mode`:

```json
{
  "script_mode": true,
  "script": [
    {"from_tick": 0, "to_tick": 5, "action": "defend", "target_snail": "B4"},
    {"from_tick": 6, "to_tick": 12, "action": "attack", "target_snail": "C7-3"},
    {"from_tick": 13, "to_tick": 999, "action": "withdraw", "target_snail": "A2"}
  ]
}
```

**Шаг 2:** В `backend/services/red_ai/runner.py`:

```python
def _run_script_mode(agent, session_tick, all_units, units_by_id) -> list:
    """Returns list of order-like dicts — skips LLM entirely."""
    script = agent.doctrine_profile.get("script", [])
    step = next((s for s in script if s["from_tick"] <= session_tick <= s["to_tick"]), None)
    if not step:
        return []
    action = step["action"]
    target_snail = step.get("target_snail")
    controlled = [units_by_id[uid] for uid in agent.controlled_unit_ids if uid in units_by_id]
    return [{"unit_id": u.id, "order_type": action, "target_snail": target_snail} for u in controlled]
```

В `run_red_agents()`, перед LLM вызовом:
```python
if agent.doctrine_profile.get("script_mode"):
    script_orders = _run_script_mode(agent, session.tick, all_units, units_by_id)
    # Apply script_orders directly, skip LLM
    continue
```

**Фронтенд:** В admin → Red AI panel → переключатель "🎬 Script Mode". При включении — textarea с JSON-скриптом. Кнопка "Validate" проверяет `JSON.parse()`.

---

### #14 — OPORD Builder (Шаблон боевого приказа)

**Файлы:** `frontend/js/scenario_builder.js`, `frontend/index.html`, `frontend/css/style.css`

В конструкторе сценариев добавить вкладку "⚔ OPORD" с пятью секциями-аккордеонами:

1. **Обстановка** — textarea: описание театра, противника, своих сил
2. **Задача** — поля: кто/что/когда/где/зачем (5W)
3. **Выполнение** — textarea: замысел командира, задачи подразделений
4. **Тыловое обеспечение** — поля: боеприпасы, топливо, медицина
5. **Управление и связь** — поля: радиосети, порядок доклада

При нажатии "Сохранить OPORD" → генерировать Markdown для `scenario.description` и обновлять `scenario.objectives` JSONB структурированными полями (`mission`, `situation`, `execution`, `service_support`, `c2`).

---

## IV. Авиация и специальные операции

---

### #15 — LZ/PZ риск высадки

**Файлы:** `backend/engine/map_objects.py`, `backend/engine/tick.py`, `frontend/js/map_objects.js`

**Шаг 1:** В `MAP_OBJECT_DEFS` добавить:
```python
"landing_zone": {
    "category": "objective",
    "geometry_type": "Point",
    "effect_radius_m": 150,
    "description": "Helicopter Landing / Pickup Zone",
    "color": "#00FF80",
},
```

Properties: `{"side": "blue"/"red"/"neutral", "suppressed": false, "suppressed_until_tick": null}`

**Шаг 2:** В `tick.py`, при обработке `air_assault`/`casevac` задач:

```python
AVIATION_LZ_RISK = 0.4   # вероятность потери за тик на подавленной LZ

def _check_lz_risk(aviation_unit, task, map_objects, tick, units_by_id) -> dict | None:
    from backend.engine.geo_utils import planar_offset_m
    from backend.engine._rng import deterministic_roll
    lz_id = task.get("lz_id")
    if not lz_id:
        return None
    lz = next((o for o in map_objects if str(o.id) == lz_id), None)
    if not lz:
        return None
    props = lz.properties or {}

    # Auto-suppress LZ if enemy units within 300m
    lz_geom = to_shape(lz.geometry)
    lz_pos  = lz_geom.centroid if lz_geom.geom_type != "Point" else lz_geom
    enemy_near = False
    for u in units_by_id.values():
        if u.is_destroyed or u.side == aviation_unit.side or u.position is None:
            continue
        _, _, d = planar_offset_m(lz_pos, to_shape(u.position))
        if d < 300:
            enemy_near = True
            break
    if enemy_near or props.get("suppressed"):
        roll = deterministic_roll(tick, aviation_unit.id)
        if roll < AVIATION_LZ_RISK:
            return {"event": "aviation_lz_casualty", "unit_id": aviation_unit.id}
    return None
```

---

### #16 — ПВО (Air Defense)

**Файлы:** `frontend/config/unit_types.json`, `backend/api/units.py` (UNIT_TYPE_SPEEDS), `backend/engine/tick.py`

**Шаг 1:** Добавить в `frontend/config/unit_types.json`:
```json
"manpads_team": {
  "label": "MANPADS Team",
  "sidc_blue": "10031000131230000000",
  "sidc_red":  "10061000131230000000",
  "speed_slow": 1.5, "speed_fast": 3.0,
  "det": 5000, "fire": 4000, "personnel": 3, "eye_height": 2.0,
  "air_defense_range_m": 4000
},
"sam_section": {
  "label": "SAM Section",
  "sidc_blue": "10031000131230000000",
  "sidc_red":  "10061000131230000000",
  "speed_slow": 2.0, "speed_fast": 5.0,
  "det": 15000, "fire": 12000, "personnel": 20, "eye_height": 5.0,
  "air_defense_range_m": 12000
}
```

**Шаг 2:** Добавить в `backend/api/units.py` → `UNIT_TYPE_SPEEDS`:
```python
"manpads_team": {"slow": 1.5, "fast": 3.0},
"sam_section":  {"slow": 2.0, "fast": 5.0},
```

**Шаг 3:** В `backend/engine/tick.py` — новый шаг `3a. Air Defense Intercept` (после movement, перед detection):

```python
AIR_DEFENSE_UNIT_TYPES = {"manpads_team", "sam_section"}
AIR_DEFENSE_RANGES_M   = {"manpads_team": 4_000, "sam_section": 12_000}
AD_INTERCEPT_BASE_PROB = 0.25
AD_DAMAGE = 0.40   # значительный урон от перехвата

def _process_air_defense(all_units: list, tick: int) -> list[dict]:
    from backend.engine.geo_utils import planar_offset_m
    from backend.engine._rng import deterministic_roll
    events = []
    ad_units  = [u for u in all_units if u.unit_type in AIR_DEFENSE_UNIT_TYPES and not u.is_destroyed]
    air_units = [u for u in all_units if u.unit_type in AVIATION_UNIT_TYPES and not u.is_destroyed]

    for ad_unit in ad_units:
        if (ad_unit.ammo or 0) <= 0:
            continue
        if ad_unit.position is None:
            continue
        ad_pos = to_shape(ad_unit.position)
        ad_range = AIR_DEFENSE_RANGES_M.get(ad_unit.unit_type, 4000)
        for aviation in air_units:
            if aviation.side == ad_unit.side or aviation.position is None:
                continue
            avn_pos = to_shape(aviation.position)
            _, _, dist_m = planar_offset_m(ad_pos, avn_pos)
            if dist_m > ad_range:
                continue
            range_factor  = 1 - dist_m / ad_range
            uav_bonus     = 1.5 if aviation.unit_type == "recon_uav" else 1.0
            intercept_prob = AD_INTERCEPT_BASE_PROB * range_factor * (ad_unit.ammo or 1.0) * uav_bonus
            roll = deterministic_roll(tick, ad_unit.id, aviation.id)
            if roll < intercept_prob:
                aviation.strength = max(0.0, aviation.strength - AD_DAMAGE)
                ad_unit.ammo = max(0.0, (ad_unit.ammo or 0) - 0.1)
                events.append({
                    "event_type": "air_defense_intercept",
                    "actor_unit_id": ad_unit.id,
                    "target_unit_id": aviation.id,
                    "payload": {"dist_m": dist_m, "damage": AD_DAMAGE},
                    "text_summary": f"{ad_unit.name} intercepted {aviation.name}",
                    "visibility": "all",
                })
    return events
```

> NB: внешний цикл `O(N_ad × N_air)` приемлем при типичных ≤20 ПВО/авиа на сессию. При масштабах >100 — заменить на STRtree (см. §0.7).

**Граничные случаи:**
- ПВО НЕ атакует наземные цели
- `recon_uav` уязвимее (множитель 1.5)
- Транспортный вертолёт без брони — нельзя ответить огнём

---

## V. Адаптивное самообучение из игровых сессий

---

### #17 — Адаптивное самообучение

> ⚠️ **Критические ограничения (читай обязательно)**
>
> Пользователи ошибаются, используют сленг, опечатки, делают семантически бессмысленные приказы.
> `order.status=completed` НЕ означает, что парсер понял команду правильно.
> Система НИКОГДА не применяет изменения автоматически — только предлагает, человек одобряет.
> Минимальное доказательство: паттерн из ≥5 разных сессий от ≥3 разных пользователей.
> `doctrine_reviewer` (дополнения FIELD_MANUAL.md) в MVP **не реализуется** — слишком высокий риск отравления доктрины.

**Новые файлы:**
- `backend/services/learning/__init__.py`
- `backend/services/learning/session_analyzer.py`
- `backend/services/learning/phrasebook_miner.py`
- `backend/services/learning/proposal_store.py`
- `backend/models/learning_proposal.py`
- `alembic/versions/008_add_learning_proposal.py`

---

#### 17a — Модель данных (`backend/models/learning_proposal.py`)

```python
import uuid, enum
from sqlalchemy import Column, String, Float, Text, Integer, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import Enum as SAEnum, DateTime
from backend.database import Base

class ProposalType(str, enum.Enum):
    phrasebook_case    = "phrasebook_case"     # новый [[case]] в TOML
    phrasebook_lexicon = "phrasebook_lexicon"  # новое слово в [lexicon.X]

class ProposalStatus(str, enum.Enum):
    pending       = "pending"
    approved      = "approved"
    rejected      = "rejected"
    applied       = "applied"
    auto_rejected = "auto_rejected"   # низкая уверенность, НЕ показывается admin

class LearningProposal(Base):
    __tablename__ = "learning_proposals"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_ids      = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    user_ids         = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    proposal_type    = Column(SAEnum(ProposalType), nullable=False)
    target_file      = Column(String(200), nullable=False)
    target_section   = Column(String(200), nullable=True)
    proposed_text    = Column(Text, nullable=False)
    rationale        = Column(Text, nullable=False)
    source_order_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    example_texts    = Column(ARRAY(Text), nullable=False)
    confidence       = Column(Float, nullable=False)
    cross_session_count  = Column(Integer, nullable=False)
    unique_user_count    = Column(Integer, nullable=False)
    llm_judge_score      = Column(Float, nullable=True)
    llm_judge_reasoning  = Column(Text, nullable=True)
    status           = Column(SAEnum(ProposalStatus), default=ProposalStatus.pending)
    created_at       = Column(DateTime, nullable=False)
    applied_at       = Column(DateTime, nullable=True)
```

Миграция `008_add_learning_proposal.py`: создать ENUM типы, затем таблицу.

---

#### 17b — `session_analyzer.py`

**Фильтры качества (все должны пройти):**

```python
MIN_TEXT_LEN        = 8
MAX_TEXT_LEN        = 250
MIN_TICKS_ALIVE     = 2     # отменённые раньше — ошибка ввода
KW_CONF_CEILING     = 0.80  # уже известен keyword-парсеру — не нужно добавлять

def extract_analyzable_orders(session_id, orders: list) -> list[dict]:
    result = []
    for order in orders:
        text   = (order.original_text or "").strip()
        parsed = order.parsed_order or {}

        # Filter 1: length
        if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
            continue
        # Filter 2: only command messages
        if parsed.get("message_type") != "command":
            continue
        # Filter 3: keyword already knows this pattern
        kw_conf = float(parsed.get("keyword_confidence", 1.0))
        if kw_conf >= KW_CONF_CEILING:
            continue
        # Filter 4: only terminal statuses
        if order.status not in ("completed", "cancelled", "failed"):
            continue
        # Filter 5: cancelled too fast = typo
        ticks_alive = _compute_ticks_alive(order, tick_interval_secs=60)
        if order.status == "cancelled" and ticks_alive < MIN_TICKS_ALIVE:
            continue
        # Filter 6: LLM was very uncertain = ambiguous input
        if parsed.get("model_tier") == "full" and kw_conf < 0.15:
            continue

        result.append({
            "order_id":          order.id,
            "session_id":        session_id,
            "user_id":           order.issued_by_user_id,
            "original_text":     text,
            "order_type":        parsed.get("order_type"),
            "keyword_confidence": kw_conf,
            "model_tier":        parsed.get("model_tier"),
            "outcome":           order.status,
            "ticks_alive":       ticks_alive,
            "language":          parsed.get("language", "ru"),
        })
    return result
```

---

#### 17c — `phrasebook_miner.py`

**Защита от отравления данными:**

```python
import re
from collections import defaultdict, Counter

MIN_CROSS_SESSIONS  = 5      # минимум 5 разных сессий
MIN_UNIQUE_USERS    = 3      # минимум 3 разных пользователя
LLM_AGREEMENT_RATE  = 0.85   # LLM 85%+ времени классифицировал одинаково
AUTO_REJECT_CONF    = 0.40
MIN_EXAMPLES        = 3

# Нормализация: убрать конкретные координаты/имена, оставить паттерн
COORD_RE    = re.compile(r'\b\d{1,2}\.\d+\b|\b[A-Z]\d{1,2}(-\d)?\b', re.I)
UNIT_NAME_RE = re.compile(r'\b(1st|2nd|3rd|\d+th|первый|второй|взвод|отдел)\s+\w+', re.I)
NUM_RE      = re.compile(r'\b\d+\b')

def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = COORD_RE.sub("[LOC]", t)
    t = UNIT_NAME_RE.sub("[UNIT]", t)
    t = NUM_RE.sub("[N]", t)
    return re.sub(r'\s+', ' ', t)

def mine_proposals(per_session_orders: list[list[dict]]) -> list[dict]:
    """
    per_session_orders: list of lists (one list per session from session_analyzer)
    Returns raw proposal candidates (before LLM judge).
    """
    groups = defaultdict(list)
    for session_orders in per_session_orders:
        for o in session_orders:
            groups[_normalize(o["original_text"])].append(o)

    proposals = []
    for norm_text, entries in groups.items():
        session_ids = list({e["session_id"] for e in entries})
        user_ids    = list({e["user_id"] for e in entries if e.get("user_id")})

        # Cross-session and cross-user requirements
        if len(session_ids) < MIN_CROSS_SESSIONS:
            continue
        if len(user_ids) < MIN_UNIQUE_USERS:
            continue

        # LLM agreement on order_type
        type_counts = Counter(e["order_type"] for e in entries)
        most_type, most_count = type_counts.most_common(1)[0]
        agreement = most_count / len(entries)
        if agreement < LLM_AGREEMENT_RATE:
            continue   # LLM disagrees — ambiguous or noisy

        # Pick best examples (prefer completed, longer texts)
        best_entries = sorted(
            [e for e in entries if e["order_type"] == most_type],
            key=lambda e: (e["outcome"] == "completed", len(e["original_text"])),
            reverse=True,
        )[:5]
        if len(best_entries) < MIN_EXAMPLES:
            continue

        confidence = min(agreement, len(session_ids) / 10.0)

        proposed_toml = (
            f'[[case]]\n'
            f'input = {repr(best_entries[0]["original_text"])}\n'
            f'order_type = "{most_type}"\n'
            f'language = "{entries[0].get("language", "ru")}"\n'
            f'# Auto-proposed ({len(session_ids)} sessions, {len(user_ids)} users). Review before applying.\n'
        )

        proposals.append({
            "proposal_type":      "phrasebook_case",
            "target_file":        "backend/data/order_phrasebook.toml",
            "proposed_text":      proposed_toml,
            "rationale":          (
                f"Pattern seen in {len(session_ids)} sessions from {len(user_ids)} users. "
                f"LLM classified as '{most_type}' {agreement:.0%} of the time. "
                f"Keyword conf avg: {sum(e['keyword_confidence'] for e in entries)/len(entries):.2f} (below ceiling)."
            ),
            "source_order_ids":   [e["order_id"] for e in entries],
            "example_texts":      [e["original_text"] for e in best_entries],
            "confidence":         confidence,
            "cross_session_count": len(session_ids),
            "unique_user_count":   len(user_ids),
            "session_ids":         session_ids,
            "user_ids":            user_ids,
        })

    return proposals
```

---

#### 17d — LLM Judge (`phrasebook_miner.py` или `judge.py`)

Вызывается ПОСЛЕ статистической фильтрации, для каждого кандидата. Использовать **GPT-4o-mini** (не reasoning модель — бинарная задача, reasoning избыточен и дорог):

```python
async def llm_judge(proposal: dict, llm_client) -> dict:
    prompt = f"""Military command pattern validation.

Pattern extracted from training exercise logs:
Main example: "{proposal['example_texts'][0]}"
Other examples: {proposal['example_texts'][1:3]}
Proposed classification: {proposal['proposed_text'].split('order_type = ')[1].split(chr(10))[0]}

Is this a legitimate general military command phrase that should be recognized by a parser?

REJECT if:
- Typo, nonsense, or incomplete phrase
- Acknowledgment or status report, not a command
- Single player's idiosyncratic expression (not general military language)
- Highly ambiguous with no clear tactical meaning

Respond ONLY as JSON: {{"is_valid": true/false, "score": 0.0-1.0, "reasoning": "1-2 sentences"}}"""

    resp = await llm_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=120,
        temperature=0.1,
    )
    import json
    data = json.loads(resp.choices[0].message.content)
    return {**proposal, "llm_judge_score": data.get("score", 0), "llm_judge_reasoning": data.get("reasoning", "")}
```

---

#### 17e — `proposal_store.py`

```python
AUTO_REJECT_CONF      = 0.40
AUTO_REJECT_LLM_SCORE = 0.60

async def save_proposals(proposals: list[dict], db: AsyncSession):
    from datetime import datetime, timezone
    for prop in proposals:
        status = "pending"
        if prop["confidence"] < AUTO_REJECT_CONF:
            status = "auto_rejected"
        elif (prop.get("llm_judge_score") or 1.0) < AUTO_REJECT_LLM_SCORE:
            status = "auto_rejected"
        record = LearningProposal(
            **{k: v for k, v in prop.items() if k in LearningProposal.__table__.columns},
            status=status,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
    await db.commit()
```

---

#### 17f — API эндпоинты в `backend/api/admin.py`

```python
@router.post("/sessions/{session_id}/analyze-learning")
async def analyze_session_learning(session_id: uuid.UUID, db = Depends(get_db)):
    """Trigger analysis. Async — returns count of new proposals."""
    # Load all orders for this session
    # Run session_analyzer.extract_analyzable_orders()
    # Load historically analyzed orders from all sessions
    # Run phrasebook_miner.mine_proposals() across ALL sessions
    # Run llm_judge on each candidate
    # Run proposal_store.save_proposals()
    return {"status": "ok", "proposals_created": N, "auto_rejected": M}

@router.get("/learning/proposals")
async def list_learning_proposals(status: str = "pending", db = Depends(get_db)):
    """Returns proposals for human review. Does NOT return auto_rejected."""
    ...

@router.patch("/learning/proposals/{proposal_id}")
async def update_proposal(proposal_id: uuid.UUID, body: dict, db = Depends(get_db)):
    """Admin approves or rejects: body = {"status": "approved"/"rejected"}"""
    ...

@router.post("/learning/apply")
async def apply_proposals(body: ApplyProposalsRequest, db = Depends(get_db)):
    """
    Applies ONLY 'approved' proposals.
    Writes to TOML file, triggers hot-reload.
    """
    for pid in body.proposal_ids:
        proposal = await db.get(LearningProposal, pid)
        if proposal.status != ProposalStatus.approved:
            raise HTTPException(400, f"Proposal {pid} is not approved")
        _append_to_toml(proposal.proposed_text)
        proposal.status = ProposalStatus.applied
        proposal.applied_at = datetime.now(timezone.utc)
    await db.commit()
    # Hot-reload
    from backend.services.order_phrasebook import reload_phrasebook
    reload_phrasebook()
    return {"applied": len(body.proposal_ids)}
```

Вспомогательная функция записи в TOML:
```python
import shutil
from pathlib import Path

PHRASEBOOK_PATH = Path("backend/data/order_phrasebook.toml")

def _append_to_toml(toml_block: str):
    shutil.copy2(PHRASEBOOK_PATH, PHRASEBOOK_PATH.with_suffix(".toml.bak"))
    with open(PHRASEBOOK_PATH, "a", encoding="utf-8") as f:
        f.write("\n# === Human-approved auto-proposal ===\n")
        f.write(toml_block)
        f.write("\n")
```

Убедись что `reload_phrasebook()` реализована в `backend/services/order_phrasebook.py` — если нет, добавить.

---

#### 17g — Фронтенд (в `admin.js`)

Новый раздел в Admin → Monitor → "🎓 Learning". Структура UI:

```
┌─ 🎓 Learning Analysis ───────────────────────────────────┐
│  [Analyze This Session]   [View All Proposals]           │
│  Pending: 3 │ Auto-rejected: 12                          │
│                                                          │
│  Proposal #1 ─────────────────────────────────────────   │
│  phrasebook_case │ EN │ order_type: move                 │
│  Confidence: 0.82  LLM Judge: 0.91                       │
│  Sources: 7 sessions, 5 users                            │
│                                                          │
│  Examples:                                               │
│    "Double-time to [LOC]"                                │
│    "Double time, move to [LOC]"                          │
│                                                          │
│  Proposed TOML: [monospace block]                        │
│  Rationale: [text]                                       │
│                                                          │
│  [✓ Approve]  [✗ Reject]                                 │
│                                                          │
│  ─────────────────────────────────────────               │
│  Approved queue: 2 proposals                             │
│  [Apply Approved Proposals]                              │
└──────────────────────────────────────────────────────────┘
```

**Важно:** Кнопка "Apply" активна только при наличии `approved` proposals. Сначала Approve, потом Apply — намеренный двухшаговый процесс.

---

#### 17h — Что НЕ реализуется (MVP)

| Запрос | Причина |
|---|---|
| `doctrine_reviewer.py` → дополнения FIELD_MANUAL.md | Слишком высокий риск отравления доктрины без накопленного объёма |
| Reasoning модель (o1/o3) для каждого кандидата | Избыточно для бинарной задачи; GPT-4o-mini достаточен |
| Автоматическое применение без одобрения | Принципиальное ограничение проекта |
| Fine-tuning LLM весов | За рамками проекта |
| Обучение на `status=cancelled` из одной сессии | Слишком мало данных; пользователь мог просто передумать |

---

## VI. UX Onboarding

---

### #18 — Tutorial / Onboarding

**Файлы:** `frontend/js/tutorial.js` (новый), `frontend/index.html`, `frontend/css/style.css`, `frontend/js/session_ui.js`, `backend/api/auth.py`, миграция `009_add_tutorial_completed.py`

**Мотивация:** Новый игрок открывает интерфейс с десятками кнопок, картой, командной панелью. Без структурированного introduction теряется за минуты.

**Архитектура:** Spotlight-подсветка элемента + tooltip с шагом + опциональное действие-триггер для автопереключения шага. Никаких внешних либ — чистый CSS/JS, ~250 строк.

**Шаг 1: Backend — флаг `tutorial_completed`**

В `backend/models/user.py` добавить:
```python
tutorial_completed = Column(Boolean, default=False, nullable=False, server_default="false")
```

Миграция `009_add_tutorial_completed.py`: `op.add_column("users", sa.Column("tutorial_completed", sa.Boolean(), server_default="false", nullable=False))`.

В `backend/schemas/auth.py: UserRead` добавить `tutorial_completed: bool`. В `/api/auth/login` и `/api/auth/register` возвращать значение.

Эндпоинт в `backend/api/auth.py`:
```python
@router.post("/auth/tutorial-complete")
async def mark_tutorial_complete(user = Depends(get_current_user), db = Depends(get_db)):
    user.tutorial_completed = True
    await db.commit()
    return {"ok": True}
```

**Шаг 2: `frontend/js/tutorial.js`**

```javascript
const KTutorial = {
    _steps: [],
    _idx: 0,
    _overlay: null,
    _box: null,

    init() {
        // Build steps array (see below)
        this._steps = this._buildSteps();
    },

    /**
     * Show steps. Auto-starts on first login if user.tutorial_completed=false.
     * Manually triggered from user menu → "Show Tutorial".
     */
    start() {
        if (this._steps.length === 0) this.init();
        this._idx = 0;
        this._buildOverlay();
        this._renderStep();
    },

    skip() {
        this._teardown();
        fetch("/api/auth/tutorial-complete", {
            method: "POST",
            headers: { "Authorization": `Bearer ${KSessionUI.getToken()}` },
        });
    },

    _next() {
        if (this._idx >= this._steps.length - 1) return this.skip();
        this._idx++;
        this._renderStep();
    },

    _prev() {
        if (this._idx === 0) return;
        this._idx--;
        this._renderStep();
    },

    _renderStep() {
        const step = this._steps[this._idx];
        const target = step.selector ? document.querySelector(step.selector) : null;
        this._highlight(target);
        this._box.querySelector(".kt-title").textContent = step.title;
        this._box.querySelector(".kt-body").innerHTML = step.body;
        this._box.querySelector(".kt-progress").textContent =
            `${this._idx + 1} / ${this._steps.length}`;
        this._positionBox(target);
        if (step.waitFor) {
            // Subscribe to a custom event that auto-advances
            const handler = () => { document.removeEventListener(step.waitFor, handler); this._next(); };
            document.addEventListener(step.waitFor, handler, { once: true });
        }
    },

    _buildOverlay() {
        if (this._overlay) return;
        this._overlay = document.createElement("div");
        this._overlay.className = "kt-overlay";
        this._overlay.innerHTML = `
          <div class="kt-spotlight"></div>
          <div class="kt-box">
            <div class="kt-progress"></div>
            <h3 class="kt-title"></h3>
            <div class="kt-body"></div>
            <div class="kt-actions">
              <button class="kt-skip">Skip</button>
              <button class="kt-prev">← Back</button>
              <button class="kt-next">Next →</button>
            </div>
          </div>`;
        document.body.appendChild(this._overlay);
        this._box = this._overlay.querySelector(".kt-box");
        this._overlay.querySelector(".kt-skip").onclick = () => this.skip();
        this._overlay.querySelector(".kt-prev").onclick = () => this._prev();
        this._overlay.querySelector(".kt-next").onclick = () => this._next();
    },

    _highlight(el) {
        const sl = this._overlay.querySelector(".kt-spotlight");
        if (!el) { sl.style.display = "none"; return; }
        const r = el.getBoundingClientRect();
        sl.style.display = "block";
        sl.style.left = `${r.left - 6}px`;
        sl.style.top  = `${r.top - 6}px`;
        sl.style.width  = `${r.width + 12}px`;
        sl.style.height = `${r.height + 12}px`;
    },

    _positionBox(target) {
        const box = this._box;
        if (!target) { // center modal
            box.style.left = "50%"; box.style.top = "50%";
            box.style.transform = "translate(-50%, -50%)";
            return;
        }
        const r = target.getBoundingClientRect();
        const margin = 16;
        box.style.transform = "none";
        box.style.left = `${Math.min(window.innerWidth - 360, r.right + margin)}px`;
        box.style.top  = `${Math.max(8, r.top)}px`;
    },

    _teardown() {
        if (this._overlay) this._overlay.remove();
        this._overlay = null;
    },

    _buildSteps() {
        return [
            {
                title: "Welcome to KShU",
                body: "This is a tactical command exercise. You will issue orders to units and watch them execute. Press Next to begin.",
            },
            {
                selector: "#map",
                title: "The Tactical Map",
                body: "Your battlefield. Drag to pan, scroll to zoom. The grid is your reference for orders (e.g. <b>F7-5</b>).",
            },
            {
                selector: ".unit-marker",   // first unit marker
                title: "Friendly Units",
                body: "Blue NATO symbols are your units. <b>Click</b> one to select it. <b>Right-click</b> opens its context menu.",
                waitFor: "kshu:unit-selected",
            },
            {
                selector: "#cmd-panel",
                title: "Command Panel",
                body: "Bottom panel — type orders here. Try: <code>Move to F7-5</code>. Press Send (or Ctrl+Enter).",
                waitFor: "kshu:order-submitted",
            },
            {
                selector: "#radio-tab",
                title: "Radio Channel",
                body: "Your unit's radio responses appear here. They acknowledge orders and report situational changes.",
            },
            {
                selector: "#orders-complete-btn",
                title: "Execute Turn",
                body: "When ready, click here to advance the simulation by one tick. Units move, fire, and detect enemies.",
                waitFor: "kshu:tick-advanced",
            },
            {
                selector: "#reports-tab",
                title: "Reports",
                body: "SPOTREP, SHELREP, SITREP — auto-generated tactical reports appear here. Click any to locate on map.",
            },
            {
                title: "You're Ready",
                body: "Tutorial complete. You can re-run it anytime from the user menu (top right). Good luck, commander.",
            },
        ];
    },
};
```

**Шаг 3: Триггер dispatching custom events** (в существующем коде):
- `units.js` после select → `document.dispatchEvent(new CustomEvent("kshu:unit-selected"))`
- `orders.js` после `submitOrder()` → `kshu:order-submitted`
- `app.js` в `tick_update` handler → `kshu:tick-advanced`

**Шаг 4: Auto-start при первом входе** (в `session_ui.js`):
```javascript
// after successful login/register
if (!user.tutorial_completed) {
    KTutorial.start();
}
```

В user-меню добавить пункт "🎓 Tutorial" → `KTutorial.start()`.

**Шаг 5: CSS (`style.css`)**:
```css
.kt-overlay      { position: fixed; inset: 0; pointer-events: none; z-index: 10000; }
.kt-overlay::before {
    content: ""; position: absolute; inset: 0;
    background: rgba(0,0,0,0.55);
    pointer-events: auto;
}
.kt-spotlight    {
    position: absolute; border-radius: 6px;
    box-shadow: 0 0 0 9999px rgba(0,0,0,0.55);
    background: transparent; pointer-events: none;
    transition: all 0.25s ease;
}
.kt-box {
    position: absolute; pointer-events: auto;
    width: 340px; padding: 18px;
    background: #18202a; color: #e8eef5;
    border: 1px solid #3a4a5e; border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    font: 14px/1.4 system-ui, sans-serif;
}
.kt-progress { font-size: 11px; color: #6c8295; margin-bottom: 6px; }
.kt-title    { margin: 0 0 8px; font-size: 16px; color: #ffd479; }
.kt-body     { margin-bottom: 14px; }
.kt-body code{ background:#2a3645; padding:2px 6px; border-radius:3px; font-size:12px; }
.kt-actions  { display: flex; justify-content: flex-end; gap: 6px; }
.kt-actions button {
    background:#2a3645; color:#e8eef5; border:1px solid #3a4a5e;
    padding: 6px 12px; border-radius:4px; cursor:pointer;
}
.kt-actions button:hover { background:#36475c; }
.kt-skip     { color:#8da4b8 !important; }
```

**Граничные случаи:**
- Если selector не найден (DOM ещё не отрисован) → `_highlight(null)` → центрированный модал
- Tutorial во время активной сессии — допустимо, не блокирует геймплей
- Mobile/маленький экран — box `position: fixed; bottom: 0`, без spotlight
- `tutorial_completed=true` существующих пользователей — миграция `server_default="false"` означает: все легаси-пользователи получат туториал на следующем логине. Если это нежелательно — после миграции `UPDATE users SET tutorial_completed = true`.

**Тест-критерий:**
- Новый зарегистрированный пользователь после успешного login → tutorial запускается автоматически
- Skip → `tutorial_completed=true` в БД, второй login туториал НЕ показывает
- "Show Tutorial" в меню → запуск независимо от флага

---

## Pre-flight Checklist (перед началом работы)

| ✓ | Проверка | Где |
|---|---|---|
| ☐ | Создан `backend/engine/geo_utils.py` | §0.1 |
| ☐ | Создан `backend/engine/_rng.py` (`deterministic_roll`) | §0.2 |
| ☐ | Все JSONB-мутации через `dict(...)` + переприсваивание | §0.3 |
| ☐ | Все новые события имеют `visibility` поле | §0.4 |
| ☐ | `defense.py` обновляет `heading_deg` для defend-задач | §0.5 |
| ☐ | Каждый новый OrderType прошёл all 7 пунктов чек-листа | §0.6 |
| ☐ | STRtree использован вместо O(N²) при N>50 юнитов | §0.7 |
| ☐ | Replay snapshots — один Event на тик с дельтой | §0.8 |
| ☐ | Phrasebook hot-reload работает (см. §0.10) | §0.10, §17 |
| ☐ | Регрессионные тесты (`test_combat`, `test_order_pipeline`) проходят | каждый § |

**Порядок реализации (рекомендуемый):**
1. §0.1, §0.2 — утилиты (без них все §-ы будут падать)
2. §1, §2, §5, §9 — низкорисковые изоляты, разогрев
3. §0.5, §0.6 — pre-requisites для §3, §4
4. §3, §4, §6, §7, §10 — глубокие боевые механики
5. §8, §11, §12, §13 — C2 и admin-инструменты
6. §15, §16 — авиация (требует §0.2)
7. §17 — самообучение (после накопления данных в реальных сессиях)
8. §18 — UX onboarding (последним, чтобы вести по уже стабильному UI)
9. §14 — OPORD builder (косметический)

