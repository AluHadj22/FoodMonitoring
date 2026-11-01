"""Направления работы на платформе (питание, целевое обучение и т.д.)."""
from __future__ import annotations

from typing import Optional

DIRECTION_FOOD = "food"
DIRECTION_TARGETED = "targeted"

DIRECTIONS = {
    DIRECTION_FOOD: {
        "id": DIRECTION_FOOD,
        "name": "Питание",
        "short": "Питание",
        "school_home": "/dashboard",
        "admin_home": "/admin",
    },
    DIRECTION_TARGETED: {
        "id": DIRECTION_TARGETED,
        "name": "Целевое обучение",
        "short": "Целевое",
        "school_home": "/targeted",
        "admin_home": "/targeted/admin",
    },
}

DEFAULT_DIRECTION = DIRECTION_FOOD
SESSION_KEY = "active_direction"


def normalize_direction(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in DIRECTIONS:
        return raw
    aliases = {
        "питание": DIRECTION_FOOD,
        "food": DIRECTION_FOOD,
        "целевое": DIRECTION_TARGETED,
        "целевое обучение": DIRECTION_TARGETED,
        "targeted": DIRECTION_TARGETED,
        "target": DIRECTION_TARGETED,
    }
    return aliases.get(raw, DEFAULT_DIRECTION)


def direction_meta(direction: Optional[str]) -> dict:
    return DIRECTIONS[normalize_direction(direction)]


def get_active_direction(request, user=None) -> str:
    """Активное направление: для муниципала — из профиля, иначе сессия → профиль → food."""
    role = getattr(user, "role", None) or ""
    user_dir = normalize_direction(getattr(user, "direction", None) or DEFAULT_DIRECTION)

    if role == "municipal_admin":
        return user_dir

    session_dir = None
    try:
        session_dir = request.session.get(SESSION_KEY)
    except Exception:
        session_dir = None

    if session_dir:
        return normalize_direction(session_dir)
    return user_dir


def set_active_direction(request, direction: str) -> str:
    d = normalize_direction(direction)
    request.session[SESSION_KEY] = d
    return d


def home_for_user(user, direction: Optional[str] = None) -> str:
    role = getattr(user, "role", "") or ""
    # Школы работают только в кабинете питания — целевое заполняют муниципалитеты
    if role == "user" or role == "":
        return f"/dashboard?uid={user.id}"

    d = normalize_direction(direction or getattr(user, "direction", None) or DEFAULT_DIRECTION)
    # Муниципал всегда в своём направлении аккаунта
    if role == "municipal_admin":
        d = normalize_direction(getattr(user, "direction", None) or d)
    meta = DIRECTIONS[d]
    if "admin" in role:
        return f"{meta['admin_home']}?admin_id={user.id}"
    return f"/dashboard?uid={user.id}"
