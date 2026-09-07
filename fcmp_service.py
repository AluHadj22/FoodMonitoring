# -*- coding: utf-8 -*-
"""Синхронизация статистики ФЦМПО с БД."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

import fcmp_parser
import models


def _bool_label(v: Optional[bool]) -> str:
    if v is True:
        return "да"
    if v is False:
        return "нет"
    return "—"


def row_to_dict(row: models.FcmpDailyStat) -> dict[str, Any]:
    label = None
    if row.stat_date:
        label = row.stat_date.strftime("%d.%m.%Y")
        if row.day_number is not None:
            label = f"{label} ({row.day_number})"
    return {
        "id": row.id,
        "date": row.stat_date.isoformat() if row.stat_date else None,
        "date_label": label,
        "day_number": row.day_number,
        "file_downloaded": row.file_downloaded,
        "file_processed": row.file_processed,
        "deadline_ok": row.deadline_ok,
        "sanpin_errors": row.sanpin_errors,
        "tm_compliance_pct": row.tm_compliance_pct,
        "file_downloaded_label": _bool_label(row.file_downloaded),
        "file_processed_label": _bool_label(row.file_processed),
        "deadline_ok_label": _bool_label(row.deadline_ok),
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
    }


def compute_summary(rows: list[models.FcmpDailyStat]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {
            "total_days": 0,
            "downloaded_pct": 0.0,
            "processed_pct": 0.0,
            "deadline_pct": 0.0,
            "avg_sanpin_errors": 0.0,
            "avg_tm_compliance_pct": 0.0,
        }

    def pct(flag):
        known = [r for r in rows if getattr(r, flag) is not None]
        if not known:
            return 0.0
        ok = sum(1 for r in known if getattr(r, flag))
        return round(100.0 * ok / len(known), 1)

    sanpin = [r.sanpin_errors for r in rows if r.sanpin_errors is not None]
    tm = [r.tm_compliance_pct for r in rows if r.tm_compliance_pct is not None]
    return {
        "total_days": total,
        "downloaded_pct": pct("file_downloaded"),
        "processed_pct": pct("file_processed"),
        "deadline_pct": pct("deadline_ok"),
        "avg_sanpin_errors": round(sum(sanpin) / len(sanpin), 2) if sanpin else 0.0,
        "avg_tm_compliance_pct": round(sum(tm) / len(tm), 1) if tm else 0.0,
    }


def sync_school_fcmp_stats(db: Session, user: models.User) -> dict[str, Any]:
    school_name = (user.unit_name or "").strip()
    if not school_name:
        raise ValueError("У школы не указано название (unit_name)")

    match, daily = fcmp_parser.fetch_school_fcmp_stats(
        school_name,
        district=(user.district or "").strip(),
    )
    now = datetime.utcnow()

    db_match = (
        db.query(models.FcmpSchoolMatch)
        .filter(models.FcmpSchoolMatch.user_id == user.id)
        .first()
    )
    if not db_match:
        db_match = models.FcmpSchoolMatch(user_id=user.id)
        db.add(db_match)

    db_match.foodblock_id = match.foodblock_id
    db_match.foodblock_label = match.label
    db_match.foodblock_rayon = match.rayon
    db_match.match_score = match.score
    db_match.matched_at = now
    db_match.last_synced_at = now
    db.flush()

    existing = {
        r.stat_date: r
        for r in db.query(models.FcmpDailyStat)
        .filter(models.FcmpDailyStat.user_id == user.id)
        .all()
    }
    seen_dates = set()
    for item in daily:
        seen_dates.add(item.stat_date)
        row = existing.get(item.stat_date)
        if not row:
            row = models.FcmpDailyStat(user_id=user.id, stat_date=item.stat_date)
            db.add(row)
            existing[item.stat_date] = row
        row.match_id = db_match.id
        row.day_number = item.day_number
        row.file_downloaded = item.file_downloaded
        row.file_processed = item.file_processed
        row.deadline_ok = item.deadline_ok
        row.sanpin_errors = item.sanpin_errors
        row.tm_compliance_pct = item.tm_compliance_pct
        row.raw_json = json.dumps(item.raw, ensure_ascii=False)
        row.synced_at = now

    # удаляем дни вне текущего ответа (учебный год пересинхронизирован)
    for d, row in list(existing.items()):
        if d not in seen_dates:
            db.delete(row)

    db.commit()

    rows = (
        db.query(models.FcmpDailyStat)
        .filter(models.FcmpDailyStat.user_id == user.id)
        .order_by(models.FcmpDailyStat.stat_date.asc())
        .all()
    )
    return {
        "ok": True,
        "region": fcmp_parser.CHECHNYA_REGION_NAME,
        "match": {
            "foodblock_id": match.foodblock_id,
            "label": match.label,
            "rayon": match.rayon,
            "score": round(match.score, 3),
        },
        "synced_at": now.isoformat(),
        "summary": compute_summary(rows),
        "rows": [row_to_dict(r) for r in rows],
    }


def get_school_fcmp_stats(db: Session, user_id: int) -> dict[str, Any]:
    db_match = (
        db.query(models.FcmpSchoolMatch)
        .filter(models.FcmpSchoolMatch.user_id == user_id)
        .first()
    )
    rows = (
        db.query(models.FcmpDailyStat)
        .filter(models.FcmpDailyStat.user_id == user_id)
        .order_by(models.FcmpDailyStat.stat_date.asc())
        .all()
    )
    return {
        "ok": True,
        "region": fcmp_parser.CHECHNYA_REGION_NAME,
        "match": None
        if not db_match
        else {
            "foodblock_id": db_match.foodblock_id,
            "label": db_match.foodblock_label,
            "rayon": db_match.foodblock_rayon,
            "score": round(db_match.match_score or 0, 3),
            "last_synced_at": db_match.last_synced_at.isoformat()
            if db_match.last_synced_at
            else None,
        },
        "summary": compute_summary(rows),
        "rows": [row_to_dict(r) for r in rows],
    }
