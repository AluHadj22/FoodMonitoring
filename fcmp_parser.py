# -*- coding: utf-8 -*-
"""Парсер статистики ФЦМПО (api.cemon.ru) для Чеченской Республики."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Optional

logger = logging.getLogger(__name__)

API_BASE = "https://api.cemon.ru"
CHECHNYA_REGION_ID = 4133
CHECHNYA_REGION_NAME = "Чеченская Республика"
USER_AGENT = "Mozilla/5.0 (compatible; FoodMonitoring/1.0)"
REFERER = "https://xn--80afhjabb0ajcdecrl4ah.xn--p1ai/"

_STOP_WORDS = {
    "мбоу", "маоу", "моу", "гбоу", "гаоу", "мкоу", "сош", "школа", "гимназия",
    "лицей", "г", "город", "имени", "им", "чеченская", "республика", "муниципальное",
    "бюджетное", "общеобразовательное", "учреждение", "средняя", "общеобразовательная",
    "с", "п", "ст", "село", "поселок", "посёлок", "станица", "аул",
}


@dataclass
class MatchedFoodblock:
    foodblock_id: int
    label: str
    rayon: str
    score: float


@dataclass
class DailyStatRow:
    stat_date: date
    day_number: Optional[int]
    file_downloaded: Optional[bool]
    file_processed: Optional[bool]
    deadline_ok: Optional[bool]
    sanpin_errors: Optional[int]
    tm_compliance_pct: Optional[float]
    foodblock_worked: Optional[str]
    raw: dict


def _http_get(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": REFERER,
            "Origin": "https://xn--80afhjabb0ajcdecrl4ah.xn--p1ai",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_get_json(url: str, timeout: int = 90) -> Any:
    raw = _http_get(url, timeout=timeout)
    if not raw or not raw.strip():
        return None
    return json.loads(raw)


def _http_post_json(url: str, payload: dict, timeout: int = 90) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": REFERER,
            "Origin": "https://xn--80afhjabb0ajcdecrl4ah.xn--p1ai",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code} от ФЦМПО: {body or e.reason}") from e
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def normalize_school_name(name: str) -> str:
    if not name:
        return ""
    s = name.casefold().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", " ", s)
    s = re.sub(r"№\s*", " ", s)
    s = re.sub(r"\bno\.\s*", " ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if t not in _STOP_WORDS and not (t.isdigit() and len(t) > 4)]
    return " ".join(tokens)


def school_similarity(a: str, b: str, district: str = "", rayon: str = "") -> float:
    na, nb = normalize_school_name(a), normalize_school_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        base = 1.0
    else:
        ratio = SequenceMatcher(None, na, nb).ratio()
        ta, tb = set(na.split()), set(nb.split())
        overlap = len(ta & tb) / max(len(ta | tb), 1) if ta and tb else 0.0
        nums_a = {t for t in ta if t.isdigit()}
        nums_b = {t for t in tb if t.isdigit()}
        num_bonus = 0.0
        if nums_a and nums_b:
            num_bonus = 0.25 if nums_a & nums_b else -0.35
        base = max(0.0, min(1.0, 0.55 * ratio + 0.45 * overlap + num_bonus))
    if district and rayon:
        nd, nr = normalize_school_name(district), normalize_school_name(rayon)
        if nd and nr and (nd in nr or nr in nd or SequenceMatcher(None, nd, nr).ratio() >= 0.7):
            base = min(1.0, base + 0.08)
    return base


def list_chechnya_foodblocks() -> list[dict]:
    data = _http_get_json(f"{API_BASE}/listpisheblok/?id={CHECHNYA_REGION_ID}")
    if not isinstance(data, list):
        raise RuntimeError(f"Неожиданный ответ listpisheblok: {type(data)}")
    return data


def find_best_foodblock(
    school_name: str,
    foodblocks: Optional[list[dict]] = None,
    district: str = "",
) -> Optional[MatchedFoodblock]:
    blocks = foodblocks if foodblocks is not None else list_chechnya_foodblocks()
    best: Optional[MatchedFoodblock] = None
    for b in blocks:
        label = str(b.get("label") or "")
        rayon = str(b.get("rayon") or "")
        score = school_similarity(school_name, label, district=district, rayon=rayon)
        if best is None or score > best.score:
            best = MatchedFoodblock(
                foodblock_id=int(b["value"]),
                label=label,
                rayon=rayon,
                score=score,
            )
    if best and best.score < 0.35:
        logger.warning("Низкое сходство школы %r -> %r (%.2f)", school_name, best.label, best.score)
    return best


def _parse_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().casefold()
    if s in {"да", "yes", "true", "1", "+", "ok", "есть"}:
        return True
    if s in {"нет", "no", "false", "0", "-", "нет данных"}:
        return False
    return None


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", ".").replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ".").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_date_and_day(value: Any, day_hint: Any = None) -> tuple[Optional[date], Optional[int]]:
    """Дата в API обычно MM/DD/YYYY; на UI — DD.MM.YYYY (N)."""
    day_num = _parse_int(day_hint)
    if value is None or value == "":
        return None, day_num
    if isinstance(value, datetime):
        return value.date(), day_num
    if isinstance(value, date):
        return value, day_num
    s = str(value).strip()
    m = re.search(r"\((\d+)\)\s*$", s)
    if m:
        day_num = int(m.group(1))
        s = s[: m.start()].strip()
    for fmt in ("%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date(), day_num
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date(), day_num
    except ValueError:
        return None, day_num


def school_year_date_range(today: Optional[date] = None) -> tuple[date, date]:
    """Учебный год: 1 сентября — 31 августа (как на сайте ФЦМПО)."""
    today = today or date.today()
    start_year = today.year if today.month >= 9 else today.year - 1
    return date(start_year, 9, 1), date(start_year + 1, 8, 31)


def _fmt_api_date(d: date) -> str:
    return d.strftime("%Y,%m,%d")


def trigger_analiz(foodblock_id: int) -> None:
    try:
        _http_get(f"{API_BASE}/logs/analiz/?id={foodblock_id}", timeout=90)
    except Exception as e:
        logger.warning("logs/analiz failed for %s: %s", foodblock_id, e)


def fetch_menustat(
    foodblock_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict]:
    if date_from is None or date_to is None:
        date_from, date_to = school_year_date_range()
    n = _fmt_api_date(date_from)
    k = _fmt_api_date(date_to)
    url = f"{API_BASE}/menustat?id={foodblock_id}&n={urllib.parse.quote(n)}&k={urllib.parse.quote(k)}"
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as e:
        logger.warning("menustat HTTP %s for %s", e.code, url)
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "rows", "items", "result"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def parse_menustat_rows(rows: list[dict]) -> list[DailyStatRow]:
    parsed: list[DailyStatRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d, day_num = _parse_date_and_day(
            row.get("Дата") or row.get("date"),
            row.get("НомерДняВМеню") or row.get("НомерДня") or row.get("k"),
        )
        if d is None:
            continue
        parsed.append(
            DailyStatRow(
                stat_date=d,
                day_number=day_num,
                file_downloaded=_parse_bool(row.get("Файл")),
                file_processed=_parse_bool(row.get("Меню")),
                deadline_ok=_parse_bool(row.get("Своевременность")),
                sanpin_errors=_parse_int(row.get("ЧислоОшибок")),
                tm_compliance_pct=_parse_float(row.get("ПроцентСоблюдения")),
                foodblock_worked=str(row.get("ПищеблокФункционировал") or "") or None,
                raw=row,
            )
        )
    parsed.sort(key=lambda x: x.stat_date)
    return parsed


def fetch_all_stats_for_foodblock(
    foodblock_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[DailyStatRow]:
    trigger_analiz(foodblock_id)
    rows = fetch_menustat(foodblock_id, date_from=date_from, date_to=date_to)
    return parse_menustat_rows(rows)


def fetch_school_fcmp_stats(
    school_name: str,
    district: str = "",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> tuple[MatchedFoodblock, list[DailyStatRow]]:
    match = find_best_foodblock(school_name, district=district)
    if not match:
        raise RuntimeError("Пищеблоки Чеченской Республики не найдены")
    rows = fetch_all_stats_for_foodblock(match.foodblock_id, date_from=date_from, date_to=date_to)
    return match, rows


def normalize_food_folder_link(link: str) -> str:
    """Ссылка для ФЦМПО: http(s)://…/food (без лишнего слэша в конце)."""
    s = (link or "").strip()
    if not s:
        raise ValueError("Пустая ссылка")
    if not (s.startswith("http://") or s.startswith("https://")):
        raise ValueError("Ссылка должна начинаться с http:// или https://")
    s = s.rstrip("/")
    if not s.endswith("/food"):
        raise ValueError("Ссылка должна заканчиваться на /food")
    return s


def get_foodblock_guid(foodblock_id: int) -> str:
    data = _http_get_json(f"{API_BASE}/perezaprosmenu/guid/?id={foodblock_id}")
    if isinstance(data, list) and data and data[0].get("Ref_Key"):
        return str(data[0]["Ref_Key"])
    raise RuntimeError(f"Не удалось получить GUID пищеблока {foodblock_id}")


def foodblock_pin_is_set(foodblock_id: int) -> bool:
    """pinchange возвращает {pin: 0|1}: 1 — пин уже задан."""
    data = _http_get_json(f"{API_BASE}/pinchange/?id={foodblock_id}")
    if isinstance(data, dict):
        return int(data.get("pin") or 0) == 1
    return False


def get_foodblock_link(foodblock_id: int) -> Optional[str]:
    data = _http_get_json(f"{API_BASE}/pishinfo?id={foodblock_id}")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("Показатель") or item.get("показатель") or "").casefold()
        if key in {"ссылка", "link", "url"}:
            val = item.get("Значение")
            return str(val).strip() if val is not None else None
    # запасной вариант: 5-й элемент как на UI ФЦМПО
    if len(data) > 4 and isinstance(data[4], dict) and data[4].get("Значение") is not None:
        return str(data[4]["Значение"]).strip()
    return None


def update_foodblock_link(foodblock_id: int, link: str, pin: str | int) -> dict[str, Any]:
    """
    Меняет ссылку пищеблока в базе ФЦМПО (как кнопка «Изменить ссылку пищеблока»).
    POST api.cemon.ru/editlink/ {guid, link, pin}
    """
    normalized = normalize_food_folder_link(link)
    guid = get_foodblock_guid(foodblock_id)
    payload = {"guid": guid, "link": normalized, "pin": str(pin)}
    result = _http_post_json(f"{API_BASE}/editlink/", payload)
    if not isinstance(result, dict):
        raise RuntimeError(f"Неожиданный ответ editlink: {result!r}")
    message = result.get("result", result)
    if isinstance(message, str) and "ошиб" in message.casefold():
        raise RuntimeError(message)
    return {
        "foodblock_id": foodblock_id,
        "guid": guid,
        "link": normalized,
        "result": message,
        "raw": result,
    }
