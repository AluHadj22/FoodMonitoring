# -*- coding: utf-8 -*-
"""Сервис конструктора онлайн-дашбордов."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

import models

_CYRILLIC_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(title: str, fallback: str = "dashboard") -> str:
    """Транслитерация кириллицы и нормализация slug."""
    text = (title or "").strip().lower()
    chars = []
    for ch in text:
        if ch in _CYRILLIC_MAP:
            chars.append(_CYRILLIC_MAP[ch])
        elif ch.isalnum():
            chars.append(ch)
        elif ch in (" ", "-", "_"):
            chars.append("-")
    slug = re.sub(r"-+", "-", "".join(chars)).strip("-")
    return slug or fallback


def unique_slug(db: Session, base: str, exclude_id: Optional[int] = None) -> str:
    slug = base
    counter = 1
    while True:
        query = db.query(models.Dashboard).filter(models.Dashboard.slug == slug)
        if exclude_id is not None:
            query = query.filter(models.Dashboard.id != exclude_id)
        if not query.first():
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _merge_settings(element_data: dict[str, Any]) -> dict[str, Any]:
    settings = element_data.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    options = element_data.get("options")
    if options is not None:
        settings = {**settings, "options": options}
    return settings


def serialize_element(element: models.DashboardElement) -> dict[str, Any]:
    content = {}
    settings = {}
    try:
        content = json.loads(element.content) if element.content else {}
    except (TypeError, json.JSONDecodeError):
        content = {}
    try:
        settings = json.loads(element.settings) if element.settings else {}
    except (TypeError, json.JSONDecodeError):
        settings = {}

    options = settings.pop("options", None) if isinstance(settings, dict) else None
    if options is None:
        options = {
            "showDataLabels": False,
            "dataLabelsColor": "#334155",
            "dataLabelsPosition": "top",
        }

    return {
        "id": element.id,
        "type": element.element_type,
        "chartType": element.chart_type,
        "title": element.title or "",
        "content": content,
        "settings": settings,
        "options": options,
        "position": {"x": element.position_x or 0, "y": element.position_y or 0},
        "size": {"w": element.width or 4, "h": element.height or 3},
    }


def serialize_dashboard(dashboard: models.Dashboard, elements: list[models.DashboardElement]) -> dict[str, Any]:
    layout = {}
    try:
        layout = json.loads(dashboard.layout_data) if dashboard.layout_data else {}
    except (TypeError, json.JSONDecodeError):
        layout = {}

    return {
        "id": dashboard.id,
        "title": dashboard.title,
        "description": dashboard.description or "",
        "slug": dashboard.slug,
        "is_published": bool(dashboard.is_published),
        "theme": dashboard.theme or "light",
        "layout": layout,
        "elements": [serialize_element(el) for el in elements],
        "updated_at": dashboard.updated_at.isoformat() if dashboard.updated_at else None,
    }


def parse_element_json_fields(element: models.DashboardElement) -> None:
    """Мутирует element: content/settings → dict для шаблонов просмотра."""
    try:
        element.content = json.loads(element.content) if element.content else {}
    except (TypeError, json.JSONDecodeError):
        element.content = {}
    try:
        element.settings = json.loads(element.settings) if element.settings else {}
    except (TypeError, json.JSONDecodeError):
        element.settings = {}
    if not isinstance(element.content, dict):
        element.content = {}
    if not isinstance(element.settings, dict):
        element.settings = {}


def save_dashboard_payload(db: Session, data: dict[str, Any]) -> models.Dashboard:
    """Создаёт или обновляет дашборд и полностью заменяет элементы."""
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Укажите название дашборда")

    description = (data.get("description") or "").strip()
    is_published = bool(data.get("is_published", False))
    theme = data.get("theme") or "light"
    layout = data.get("layout") or {}
    elements_payload = data.get("elements") or []

    dashboard_id = data.get("id")
    now = datetime.utcnow()

    if dashboard_id:
        dashboard = db.query(models.Dashboard).filter(models.Dashboard.id == dashboard_id).first()
        if not dashboard:
            raise LookupError("Дашборд не найден")

        dashboard.title = title
        dashboard.description = description
        dashboard.updated_at = now
        dashboard.layout_data = json.dumps(layout, ensure_ascii=False)
        dashboard.is_published = is_published
        dashboard.theme = theme

        if data.get("slug"):
            desired = slugify(str(data["slug"]))
            dashboard.slug = unique_slug(db, desired, exclude_id=dashboard.id)

        db.query(models.DashboardElement).filter(
            models.DashboardElement.dashboard_id == dashboard.id
        ).delete()
    else:
        slug_base = slugify(data.get("slug") or title)
        slug = unique_slug(db, slug_base)
        dashboard = models.Dashboard(
            title=title,
            description=description,
            slug=slug,
            created_at=now,
            updated_at=now,
            layout_data=json.dumps(layout, ensure_ascii=False),
            is_published=is_published,
            theme=theme,
        )
        db.add(dashboard)
        db.flush()

    for idx, element_data in enumerate(elements_payload):
        if not isinstance(element_data, dict):
            continue
        position = element_data.get("position") or {}
        size = element_data.get("size") or {}
        settings = _merge_settings(element_data)
        element = models.DashboardElement(
            dashboard_id=dashboard.id,
            element_type=element_data.get("type") or "text",
            chart_type=element_data.get("chartType"),
            title=element_data.get("title") or "",
            content=json.dumps(element_data.get("content") or {}, ensure_ascii=False),
            settings=json.dumps(settings, ensure_ascii=False),
            position_x=int(position.get("x", 0) or 0),
            position_y=int(position.get("y", 0) or 0),
            width=max(1, int(size.get("w", 4) or 4)),
            height=max(1, int(size.get("h", 3) or 3)),
            order_index=idx,
        )
        db.add(element)

    db.commit()
    db.refresh(dashboard)
    return dashboard
