"""Сервис целевого обучения: сиды, квоты, заявки, материалы."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

import models

BASE_DIR = Path(__file__).resolve().parent
SEED_PATH = BASE_DIR / "data" / "targeted_seed.json"
MATERIALS_DIR = BASE_DIR / "целевое"
DEFAULT_YEAR = 2027

LEVEL_LABELS = {
    "bachelor": "Бакалавриат",
    "master": "Магистратура",
    "specialist": "Специалитет",
    "college": "Колледж",
}

# Канонические названия — как у муниципалов при регистрации (DISTRICTS в main.py)
CANONICAL_DISTRICTS = [
    "Аргун",
    "Ачхой-Мартановский",
    "Веденский",
    "Грозненский",
    "Грозный",
    "Гудермесский",
    "Итум-Калинский",
    "Курчалоевский",
    "Надтеречный",
    "Наурский",
    "Ножай-Юртовский",
    "Серноводский",
    "Урус-Мартановский",
    "Шалинский",
    "Шаройский",
    "Шатойский",
    "Шелковской",
]


def district_key(name: Optional[str]) -> str:
    """Нормализация для сопоставления «Шалинский» ↔ «Шалинский район», «г. Грозный» ↔ «Грозный»."""
    s = (name or "").strip().lower().replace("ё", "е")
    s = s.replace("«", "").replace("»", "").replace('"', "").replace("'", "")
    # Только префикс города «г.» / «г » — не одиночную букву «г» в «Грозный»
    s = re.sub(r"^г\.\s*", "", s)
    s = re.sub(r"^г\s+", "", s)
    s = re.sub(r"\s+муниципальный\s+район$", "", s)
    s = re.sub(r"\s+район$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_CANONICAL_BY_KEY = {district_key(d): d for d in CANONICAL_DISTRICTS}


def canonical_district_name(name: Optional[str]) -> Optional[str]:
    key = district_key(name)
    if not key:
        return None
    if key in _CANONICAL_BY_KEY:
        return _CANONICAL_BY_KEY[key]
    # ГБОУ и прочие особые строки оставляем как есть
    cleaned = (name or "").strip()
    return cleaned or None

# Профили ЕНЦ (естественно-научный цикл) — по приложению к приказу № 1081-п
# «О выплате стипендий Главы ЧР» (предметы/профили, не список студентов).
ENC_PROFILE_MARKERS = [
    ("математика", "информатика"),
    ("математика", "дополнительное образование", "программирование"),
    ("физика", "робототехника"),
    ("химия", "биология"),
    ("биология", "география"),
    ("биология", "основы безопасности"),
]


def _norm_profile(text: Optional[str]) -> str:
    s = (text or "").lower().replace("ё", "е")
    s = s.replace("«", " ").replace("»", " ").replace('"', " ").replace("'", " ")
    s = s.replace("3-d", "3d").replace("3‑d", "3d").replace("3–d", "3d")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def profile_is_enc(profile: Optional[str], name: Optional[str] = None) -> bool:
    hay = _norm_profile(f"{name or ''} {profile or ''}")
    if not hay:
        return False
    for markers in ENC_PROFILE_MARKERS:
        if all(m in hay for m in markers):
            return True
    return False


# Материалы, которые раздаёт региональный координатор муниципалитетам.
# Пример распределения квот сюда не входит — квоты задаются в кабинете регионала.
MATERIALS = [
    {
        "id": "programs",
        "title": "Перечень программ ЧГПУ на 2026–2027",
        "file": "ПЕРЕЧЕНЬ ПРОГРАММ НА 2026-27 г. НАБОРА для Алихана.docx",
        "kind": "docx",
    },
    {
        "id": "exams_bachelor",
        "title": "Перечень вступительных испытаний (бакалавриат и специалитет)",
        "file": "Перечень вступительных испытаний Бакалавриат и Специалитет.pdf",
        "kind": "pdf",
    },
    {
        "id": "exams_bs",
        "title": "Приложение 1 — перечень ВИ БС 2026–2027",
        "file": "111 ПРИЛОЖЕНИЕ 1 ПЕРЕЧЕНЬ ВИ БС НА 2026-2027 уч.год_19.01.26 .pdf",
        "kind": "pdf",
    },
]


def load_seed() -> dict:
    if not SEED_PATH.exists():
        return {"year": DEFAULT_YEAR, "programs": [], "quotas": []}
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def ensure_programs_seeded(db: Session) -> int:
    count = db.query(models.TargetProgram).count()
    added = 0
    if count == 0:
        seed = load_seed()
        for i, item in enumerate(seed.get("programs") or []):
            code = (item.get("code") or "").strip()
            name = (item.get("name") or "").strip()
            if not code or not name:
                continue
            profile = (item.get("profile") or "").strip() or None
            db.add(
                models.TargetProgram(
                    code=code,
                    name=name,
                    profile=profile,
                    form=(item.get("form") or "").strip() or None,
                    level=(item.get("level") or "bachelor").strip(),
                    order_index=i,
                    is_active=True,
                    is_enc=profile_is_enc(profile, name),
                )
            )
            added += 1
        if added:
            db.commit()
    ensure_enc_flags(db)
    return added


def ensure_enc_flags(db: Session) -> int:
    """Проставить is_enc по профилям из приказа 1081-п."""
    updated = 0
    rows = db.query(models.TargetProgram).all()
    for row in rows:
        flag = profile_is_enc(row.profile, row.name)
        if bool(row.is_enc) != flag:
            row.is_enc = flag
            updated += 1
    if updated:
        db.commit()
    return updated


def ensure_quotas_seeded(db: Session, year: int = DEFAULT_YEAR, region: str = "Чеченская Республика") -> int:
    exists = db.query(models.TargetQuota).filter(models.TargetQuota.year == year).count()
    added = 0
    if exists == 0:
        seed = load_seed()
        for item in seed.get("quotas") or []:
            raw = (item.get("district") or "").strip()
            district = canonical_district_name(raw) or raw
            if not district:
                continue
            # не дублируем канонические районы
            if (
                db.query(models.TargetQuota)
                .filter(models.TargetQuota.year == year, models.TargetQuota.district == district)
                .first()
            ):
                continue
            db.add(
                models.TargetQuota(
                    year=year,
                    region=region,
                    district=district,
                    quota_total=int(item.get("quota") or 0),
                    quota_ppk=int(item.get("quota_ppk") or 0),
                )
            )
            added += 1
        if added:
            db.commit()

    # Всегда выравниваем имена под муниципалов и досоздаём пустые строки
    align_quotas_with_municipal_districts(db, year=year, region=region)
    return added


def align_quotas_with_municipal_districts(
    db: Session,
    year: int = DEFAULT_YEAR,
    region: str = "Чеченская Республика",
) -> None:
    """Мягкое выравнивание: переименовать алиасы, слить дубли, досоздать пустые строки.

    Важно: не пересоздаём все строки — иначе id меняются и POST-форма регионала
    сохраняет «в пустоту», а значения откатываются к старым.
    """
    rows = (
        db.query(models.TargetQuota)
        .filter(models.TargetQuota.year == year)
        .order_by(models.TargetQuota.id)
        .all()
    )

    by_key: dict[str, models.TargetQuota] = {}
    changed = False

    for row in rows:
        key = district_key(row.district)
        if not key:
            continue
        canon = _CANONICAL_BY_KEY.get(key)
        if not canon:
            continue

        if key in by_key:
            keep = by_key[key]
            new_total = max(int(keep.quota_total or 0), int(row.quota_total or 0))
            new_ppk = max(int(keep.quota_ppk or 0), int(row.quota_ppk or 0))
            if new_total != int(keep.quota_total or 0) or new_ppk != int(keep.quota_ppk or 0):
                keep.quota_total = new_total
                keep.quota_ppk = new_ppk
                changed = True
            if keep.district != canon:
                keep.district = canon
                changed = True
            if not (keep.region or "").strip():
                keep.region = region
                changed = True
            db.delete(row)
            changed = True
            continue

        if row.district != canon:
            # если каноническое имя уже занято другой строкой — сольём ниже при её проходе
            occupied = (
                db.query(models.TargetQuota)
                .filter(
                    models.TargetQuota.year == year,
                    models.TargetQuota.district == canon,
                    models.TargetQuota.id != row.id,
                )
                .first()
            )
            if occupied:
                occupied.quota_total = max(int(occupied.quota_total or 0), int(row.quota_total or 0))
                occupied.quota_ppk = max(int(occupied.quota_ppk or 0), int(row.quota_ppk or 0))
                if not (occupied.region or "").strip():
                    occupied.region = row.region or region
                by_key[key] = occupied
                db.delete(row)
                changed = True
                continue
            row.district = canon
            changed = True

        if not (row.region or "").strip():
            row.region = region
            changed = True
        by_key[key] = row

    for name in CANONICAL_DISTRICTS:
        key = district_key(name)
        if key in by_key:
            continue
        exists = (
            db.query(models.TargetQuota)
            .filter(models.TargetQuota.year == year, models.TargetQuota.district == name)
            .first()
        )
        if exists:
            by_key[key] = exists
            continue
        db.add(
            models.TargetQuota(
                year=year,
                region=region,
                district=name,
                quota_total=0,
                quota_ppk=0,
            )
        )
        changed = True

    if changed:
        db.commit()
    else:
        db.flush()


def list_programs(db: Session, active_only: bool = True):
    q = db.query(models.TargetProgram)
    if active_only:
        q = q.filter(models.TargetProgram.is_active.is_(True))
    return q.order_by(models.TargetProgram.level, models.TargetProgram.order_index, models.TargetProgram.code).all()


def program_label(program: models.TargetProgram) -> str:
    profile = (program.profile or "").strip()
    base = f"{program.code} — {program.name}"
    if profile:
        return f"{base}: {profile}"
    return base


def list_quotas(db: Session, year: int, region: Optional[str] = None):
    q = db.query(models.TargetQuota).filter(models.TargetQuota.year == year)
    if region:
        # регион у пользователей иногда с пробелом на конце
        q = q.filter(
            or_(
                models.TargetQuota.region == region,
                models.TargetQuota.region == (region or "").strip(),
                models.TargetQuota.region.is_(None),
            )
        )
    # канонические муниципалитеты первыми, особые (ГБОУ) в конце
    rows = q.all()

    def sort_key(item: models.TargetQuota):
        key = district_key(item.district)
        if key in _CANONICAL_BY_KEY:
            return (0, _CANONICAL_BY_KEY[key])
        return (1, item.district or "")

    return sorted(rows, key=sort_key)


def quota_for_district(db: Session, year: int, district: str) -> Optional[models.TargetQuota]:
    name = (district or "").strip()
    if not name:
        return None
    canon = canonical_district_name(name) or name
    exact = (
        db.query(models.TargetQuota)
        .filter(models.TargetQuota.year == year, models.TargetQuota.district == canon)
        .first()
    )
    if exact:
        return exact
    # запасной путь: сравнение по нормализованному ключу
    want = district_key(name)
    for row in db.query(models.TargetQuota).filter(models.TargetQuota.year == year).all():
        if district_key(row.district) == want:
            return row
    return None


def save_quotas_from_form(
    db: Session,
    *,
    year: int,
    admin: models.User,
    form_data,
) -> int:
    """Сохранить квоты из POST-формы.

    Поля: quota_d_<district>, ppk_d_<district> (основной путь)
    и устаревшие quota_<id>, ppk_<id> как запасной.
    """
    quotas = list_quotas(db, year)
    by_id = {q.id: q for q in quotas}
    by_key = {district_key(q.district): q for q in quotas}
    updated = 0
    region = (admin.region or "").strip() or "Чеченская Республика"

    def _apply(item: models.TargetQuota, total_raw, ppk_raw) -> None:
        nonlocal updated
        try:
            total_val = int(total_raw) if total_raw not in (None, "") else 0
        except (TypeError, ValueError):
            return
        try:
            ppk_val = int(ppk_raw) if ppk_raw not in (None, "") else 0
        except (TypeError, ValueError):
            ppk_val = int(item.quota_ppk or 0)

        item.quota_total = max(0, total_val)
        item.quota_ppk = max(0, min(ppk_val, item.quota_total))
        item.updated_by = admin.id
        item.region = region
        canon = canonical_district_name(item.district)
        if canon:
            item.district = canon
        updated += 1

    # основной путь — по названию района
    for key in list(form_data.keys()):
        if not key.startswith("quota_d_"):
            continue
        district_name = key[len("quota_d_") :]
        item = by_key.get(district_key(district_name))
        if not item:
            # на всякий случай создаём строку
            canon = canonical_district_name(district_name) or district_name.strip()
            if not canon:
                continue
            item = models.TargetQuota(
                year=year,
                region=region,
                district=canon,
                quota_total=0,
                quota_ppk=0,
            )
            db.add(item)
            db.flush()
            by_key[district_key(canon)] = item
        _apply(item, form_data.get(key), form_data.get(f"ppk_d_{district_name}"))

    # запасной путь — по id (если в форме ещё старые имена полей)
    if updated == 0:
        for key in list(form_data.keys()):
            if not key.startswith("quota_") or key.startswith("quota_d_"):
                continue
            raw_id = key[len("quota_") :]
            try:
                qid = int(raw_id)
            except (TypeError, ValueError):
                continue
            item = by_id.get(qid)
            if not item:
                continue
            _apply(item, form_data.get(key), form_data.get(f"ppk_{qid}"))

    db.commit()
    return updated


def quota_progress(
    quota: Optional[models.TargetQuota],
    used: int,
    ppk_used: int = 0,
    enc_used: int = 0,
) -> dict:
    """Процент выполнения квоты района для диаграммы."""
    total = int(quota.quota_total or 0) if quota else 0
    used_n = max(0, int(used or 0))
    ppk_used_n = max(0, int(ppk_used or 0))
    enc_used_n = max(0, int(enc_used or 0))
    left = max(0, total - used_n) if total else 0
    over = max(0, used_n - total) if total else 0
    if total > 0:
        pct = min(100.0, round(100.0 * used_n / total, 1))
        pct_raw = round(100.0 * used_n / total, 1)
    else:
        pct = 0.0
        pct_raw = 0.0
    return {
        "total": total,
        "used": used_n,
        "left": left,
        "over": over,
        "pct": pct,
        "pct_raw": pct_raw,
        "ppk": int(quota.quota_ppk or 0) if quota else 0,
        "ppk_used": ppk_used_n,
        "enc_used": enc_used_n,
        "has_quota": bool(quota and total > 0),
    }


def applications_sum_for_district(
    db: Session,
    year: int,
    district: str,
    *,
    ppk_only: bool = False,
    enc_only: bool = False,
) -> int:
    query = db.query(func.coalesce(func.sum(models.TargetApplication.applicants_count), 0)).filter(
        models.TargetApplication.year == year,
        models.TargetApplication.district == district,
    )
    if ppk_only:
        query = query.filter(models.TargetApplication.is_ppk.is_(True))
    if enc_only:
        query = query.join(
            models.TargetProgram,
            models.TargetApplication.program_id == models.TargetProgram.id,
        ).filter(models.TargetProgram.is_enc.is_(True))
    return int(query.scalar() or 0)


def applications_sum(
    db: Session,
    year: int,
    *,
    district: Optional[str] = None,
    region: Optional[str] = None,
    ppk_only: bool = False,
    enc_only: bool = False,
) -> int:
    query = db.query(func.coalesce(func.sum(models.TargetApplication.applicants_count), 0)).filter(
        models.TargetApplication.year == year,
    )
    if district:
        query = query.filter(models.TargetApplication.district == district)
    if region:
        query = query.filter(models.TargetApplication.region == region)
    if ppk_only:
        query = query.filter(models.TargetApplication.is_ppk.is_(True))
    if enc_only:
        query = query.join(
            models.TargetProgram,
            models.TargetApplication.program_id == models.TargetProgram.id,
        ).filter(models.TargetProgram.is_enc.is_(True))
    return int(query.scalar() or 0)


def list_applications(
    db: Session,
    year: int,
    *,
    district: Optional[str] = None,
    school_user_id: Optional[int] = None,
    region: Optional[str] = None,
    program_id: Optional[int] = None,
    school_name: Optional[str] = None,
    q: Optional[str] = None,
    enc_only: bool = False,
    ppk_only: bool = False,
):
    query = (
        db.query(models.TargetApplication)
        .options(joinedload(models.TargetApplication.program))
        .filter(models.TargetApplication.year == year)
    )
    if district:
        query = query.filter(models.TargetApplication.district == district)
    if school_user_id is not None:
        query = query.filter(models.TargetApplication.school_user_id == school_user_id)
    if region:
        query = query.filter(models.TargetApplication.region == region)
    if program_id:
        query = query.filter(models.TargetApplication.program_id == int(program_id))
    if school_name:
        query = query.filter(models.TargetApplication.school_name == school_name)
    if ppk_only:
        query = query.filter(models.TargetApplication.is_ppk.is_(True))
    if enc_only:
        query = query.join(
            models.TargetProgram,
            models.TargetApplication.program_id == models.TargetProgram.id,
        ).filter(models.TargetProgram.is_enc.is_(True))
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        query = query.filter(
            or_(
                models.TargetApplication.school_name.ilike(like),
                models.TargetApplication.program_label.ilike(like),
                models.TargetApplication.district.ilike(like),
                models.TargetApplication.note.ilike(like),
            )
        )
    return query.order_by(models.TargetApplication.updated_at.desc()).all()


def application_is_enc(app: models.TargetApplication) -> bool:
    prog = getattr(app, "program", None)
    if prog is not None and getattr(prog, "is_enc", False):
        return True
    return profile_is_enc(getattr(app, "program_label", None))


def build_dashboard_stats(
    db: Session,
    year: int,
    *,
    region: Optional[str] = None,
    district: Optional[str] = None,
) -> dict:
    """Сводка для дашборда: квоты, ППК, ЕНЦ."""
    if district:
        quotas = []
        q = quota_for_district(db, year, district)
        if q:
            quotas = [q]
    else:
        quotas = list_quotas(db, year, region=region)
        if not quotas:
            quotas = list_quotas(db, year)

    quota_total = sum(int(q.quota_total or 0) for q in quotas)
    quota_ppk = sum(int(q.quota_ppk or 0) for q in quotas)
    used = applications_sum(db, year, region=region, district=district)
    used_ppk = applications_sum(db, year, region=region, district=district, ppk_only=True)
    used_enc = applications_sum(db, year, region=region, district=district, enc_only=True)

    by_district = []
    for item in quotas:
        d = item.district
        u = applications_sum_for_district(db, year, d)
        u_ppk = applications_sum_for_district(db, year, d, ppk_only=True)
        u_enc = applications_sum_for_district(db, year, d, enc_only=True)
        total = int(item.quota_total or 0)
        by_district.append(
            {
                "district": d,
                "quota_total": total,
                "quota_ppk": int(item.quota_ppk or 0),
                "used": u,
                "used_ppk": u_ppk,
                "used_enc": u_enc,
                "left": max(0, total - u),
                "pct": min(100.0, round(100.0 * u / total, 1)) if total else 0.0,
            }
        )

    enc_programs = (
        db.query(models.TargetProgram)
        .filter(models.TargetProgram.is_enc.is_(True), models.TargetProgram.is_active.is_(True))
        .order_by(models.TargetProgram.order_index, models.TargetProgram.code)
        .all()
    )

    return {
        "quota_total": quota_total,
        "quota_ppk": quota_ppk,
        "used": used,
        "used_ppk": used_ppk,
        "used_enc": used_enc,
        "left": max(0, quota_total - used),
        "pct": min(100.0, round(100.0 * used / quota_total, 1)) if quota_total else 0.0,
        "pct_ppk": min(100.0, round(100.0 * used_ppk / quota_ppk, 1)) if quota_ppk else 0.0,
        "pct_enc": min(100.0, round(100.0 * used_enc / used, 1)) if used else 0.0,
        "by_district": by_district,
        "enc_programs": enc_programs,
        "enc_program_count": len(enc_programs),
    }


def distinct_application_schools(
    db: Session,
    year: int,
    *,
    district: Optional[str] = None,
    region: Optional[str] = None,
) -> list:
    query = db.query(models.TargetApplication.school_name).filter(
        models.TargetApplication.year == year,
        models.TargetApplication.school_name.isnot(None),
    )
    if district:
        query = query.filter(models.TargetApplication.district == district)
    if region:
        query = query.filter(models.TargetApplication.region == region)
    rows = query.distinct().order_by(models.TargetApplication.school_name).all()
    return [r[0] for r in rows if r and r[0]]


def distinct_application_districts(db: Session, year: int, region: Optional[str] = None) -> list:
    query = db.query(models.TargetApplication.district).filter(
        models.TargetApplication.year == year,
        models.TargetApplication.district.isnot(None),
    )
    if region:
        query = query.filter(models.TargetApplication.region == region)
    rows = query.distinct().order_by(models.TargetApplication.district).all()
    return [r[0] for r in rows if r and r[0]]


def build_applications_workbook(applications: list, year: int):
    """Собрать xlsx со сводом заявок."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Заявки"
    headers = [
        "№",
        "Год",
        "Район",
        "Школа",
        "Код программы",
        "Программа",
        "Количество",
        "ППК",
        "ЕНЦ",
        "Примечание",
        "Обновлено",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for i, a in enumerate(applications, 1):
        code = ""
        if getattr(a, "program", None) is not None:
            code = a.program.code or ""
        elif a.program_label and "—" in a.program_label:
            code = a.program_label.split("—", 1)[0].strip()
        ws.append(
            [
                i,
                year,
                a.district or "",
                a.school_name or "",
                code,
                a.program_label or "",
                int(a.applicants_count or 0),
                "да" if getattr(a, "is_ppk", False) else "нет",
                "да" if application_is_enc(a) else "нет",
                a.note or "",
                a.updated_at.strftime("%d.%m.%Y %H:%M") if a.updated_at else "",
            ]
        )

    # summary sheet by program
    ws2 = wb.create_sheet("Свод по программам")
    ws2.append(["Программа", "Количество заявок"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    totals = {}
    for a in applications:
        key = a.program_label or "Без программы"
        totals[key] = totals.get(key, 0) + int(a.applicants_count or 0)
    for key, val in sorted(totals.items(), key=lambda x: (-x[1], x[0])):
        ws2.append([key, val])

    ws3 = wb.create_sheet("Свод по школам")
    ws3.append(["Район", "Школа", "Количество заявок"])
    for cell in ws3[1]:
        cell.font = Font(bold=True)
    by_school = {}
    for a in applications:
        key = (a.district or "", a.school_name or "")
        by_school[key] = by_school.get(key, 0) + int(a.applicants_count or 0)
    for (district, school), val in sorted(by_school.items(), key=lambda x: (x[0][0], x[0][1])):
        ws3.append([district, school, val])

    for sheet in (ws, ws2, ws3):
        for col in sheet.columns:
            width = 12
            for cell in col:
                width = max(width, min(48, len(str(cell.value or "")) + 2))
            sheet.column_dimensions[col[0].column_letter].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def material_path(file_name: str) -> Optional[Path]:
    path = MATERIALS_DIR / file_name
    if path.exists() and path.is_file():
        return path
    # fallback: search by exact name in folder
    for p in MATERIALS_DIR.iterdir():
        if p.name == file_name:
            return p
    return None


def available_materials() -> list:
    items = []
    for m in MATERIALS:
        path = material_path(m["file"])
        items.append({**m, "available": bool(path), "url": f"/targeted/materials/{m['id']}" if path else None})
    return items
