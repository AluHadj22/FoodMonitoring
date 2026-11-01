"""Маршруты раздела «Целевое обучение»."""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import models
import targeted_service as svc
from database import get_db
from directions import (
    DIRECTION_FOOD,
    DIRECTION_TARGETED,
    DIRECTIONS,
    get_active_direction,
    home_for_user,
    normalize_direction,
    set_active_direction,
)

router = APIRouter(prefix="/targeted", tags=["targeted"])
templates = Jinja2Templates(directory="templates")


def _programs_by_level(programs):
    grouped = OrderedDict()
    for p in programs:
        grouped.setdefault(p.level or "bachelor", []).append(p)
    return grouped


def _user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.id == user_id).first()


def _require_user(db: Session, request: Request, uid: Optional[int] = None) -> Optional[models.User]:
    sid = request.session.get("user_id")
    target_id = uid or sid
    if not target_id:
        return None
    return _user_by_id(db, int(target_id))


def _is_admin(user: models.User) -> bool:
    return "admin" in (user.role or "")


def _ctx(request: Request, user: models.User, **extra):
    direction = get_active_direction(request, user)
    return {
        "request": request,
        "user": user,
        "active_direction": direction,
        "directions": DIRECTIONS,
        "year": svc.DEFAULT_YEAR,
        "level_labels": svc.LEVEL_LABELS,
        **extra,
    }


def _filter_query(**kwargs) -> str:
    clean = {k: v for k, v in kwargs.items() if v not in (None, "", 0, "0")}
    return urlencode(clean)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def targeted_home(
    request: Request,
    uid: Optional[int] = None,
    admin_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = await run_in_threadpool(_require_user, db, request, uid or admin_id)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # школы в целевое не пускаем
    if not _is_admin(user):
        return RedirectResponse(f"/dashboard?uid={user.id}", status_code=303)
    return RedirectResponse(f"/targeted/admin?admin_id={user.id}", status_code=303)


def _truthy_flag(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _optional_int(value: Optional[str]) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@router.get("/admin", response_class=HTMLResponse)
async def targeted_admin(
    request: Request,
    admin_id: int,
    q: str = "",
    program_id: Optional[str] = None,
    school_name: str = "",
    district: str = "",
    enc: str = "",
    ppk: str = "",
    db: Session = Depends(get_db),
):
    await run_in_threadpool(svc.ensure_programs_seeded, db)
    await run_in_threadpool(svc.ensure_quotas_seeded, db)

    admin = await run_in_threadpool(_user_by_id, db, admin_id)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=303)

    set_active_direction(request, DIRECTION_TARGETED)
    year = svc.DEFAULT_YEAR
    programs = await run_in_threadpool(svc.list_programs, db)
    enc_programs = [p for p in programs if getattr(p, "is_enc", False)]
    program_id_val = _optional_int(program_id)
    enc_only = _truthy_flag(enc)
    ppk_only = _truthy_flag(ppk)

    if admin.role == "municipal_admin":
        apps = await run_in_threadpool(
            lambda: svc.list_applications(
                db,
                year,
                district=admin.district or "",
                program_id=program_id_val,
                school_name=school_name or None,
                q=q or None,
                enc_only=enc_only,
                ppk_only=ppk_only,
            )
        )
        quota = await run_in_threadpool(
            lambda: svc.quota_for_district(db, year, admin.district or "")
        )
        used = await run_in_threadpool(
            lambda: svc.applications_sum_for_district(db, year, admin.district or "")
        )
        used_ppk = await run_in_threadpool(
            lambda: svc.applications_sum_for_district(
                db, year, admin.district or "", ppk_only=True
            )
        )
        used_enc = await run_in_threadpool(
            lambda: svc.applications_sum_for_district(
                db, year, admin.district or "", enc_only=True
            )
        )
        progress = svc.quota_progress(quota, used, ppk_used=used_ppk, enc_used=used_enc)
        schools = await run_in_threadpool(
            lambda: db.query(models.User)
            .filter(
                models.User.role == "user",
                models.User.district == admin.district,
            )
            .order_by(models.User.unit_name)
            .all()
        )
        school_options = await run_in_threadpool(
            lambda: svc.distinct_application_schools(db, year, district=admin.district or "")
        )
        programs_payload = [
            {
                "id": p.id,
                "code": p.code or "",
                "name": p.name or "",
                "profile": p.profile or "",
                "level": p.level or "bachelor",
                "label": svc.program_label(p),
                "level_label": svc.LEVEL_LABELS.get(p.level or "bachelor", p.level or ""),
                "is_enc": bool(getattr(p, "is_enc", False)),
            }
            for p in programs
        ]
        export_qs = _filter_query(
            admin_id=admin_id,
            q=q,
            program_id=program_id_val,
            school_name=school_name,
            enc=1 if enc_only else None,
            ppk=1 if ppk_only else None,
        )
        export_enc_qs = _filter_query(
            admin_id=admin_id,
            q=q,
            program_id=program_id_val,
            school_name=school_name,
            enc=1,
            ppk=1 if ppk_only else None,
        )
        return templates.TemplateResponse(
            "targeted_municipal.html",
            _ctx(
                request,
                admin,
                applications=apps,
                quota=quota,
                district_used=used,
                progress=progress,
                schools=schools,
                school_options=school_options,
                programs=programs,
                enc_programs=enc_programs,
                programs_by_level=_programs_by_level(programs),
                programs_json=json.dumps(programs_payload, ensure_ascii=False)
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"),
                materials=svc.available_materials(),
                filters={
                    "q": q or "",
                    "program_id": program_id_val or "",
                    "school_name": school_name or "",
                    "district": "",
                    "enc": enc_only,
                    "ppk": ppk_only,
                },
                export_url=f"/targeted/export.xlsx?{export_qs}",
                export_enc_url=f"/targeted/export.xlsx?{export_enc_qs}",
                app_is_enc=svc.application_is_enc,
            ),
        )

    # regional
    quotas = await run_in_threadpool(lambda: svc.list_quotas(db, year, region=admin.region))
    if not quotas:
        quotas = await run_in_threadpool(lambda: svc.list_quotas(db, year))

    apps = await run_in_threadpool(
        lambda: svc.list_applications(
            db,
            year,
            region=admin.region,
            district=district or None,
            program_id=program_id_val,
            school_name=school_name or None,
            q=q or None,
            enc_only=enc_only,
            ppk_only=ppk_only,
        )
    )
    dash = await run_in_threadpool(
        lambda: svc.build_dashboard_stats(db, year, region=admin.region)
    )
    progress = []
    for item in quotas:
        used_n = await run_in_threadpool(
            lambda d=item.district: svc.applications_sum_for_district(db, year, d)
        )
        used_ppk_n = await run_in_threadpool(
            lambda d=item.district: svc.applications_sum_for_district(
                db, year, d, ppk_only=True
            )
        )
        used_enc_n = await run_in_threadpool(
            lambda d=item.district: svc.applications_sum_for_district(
                db, year, d, enc_only=True
            )
        )
        progress.append(
            {
                "quota": item,
                "used": used_n,
                "used_ppk": used_ppk_n,
                "used_enc": used_enc_n,
                "left": max(0, (item.quota_total or 0) - used_n),
            }
        )

    school_options = await run_in_threadpool(
        lambda: svc.distinct_application_schools(
            db, year, region=admin.region, district=district or None
        )
    )
    district_options = await run_in_threadpool(
        lambda: svc.distinct_application_districts(db, year, region=admin.region)
    )
    # also include quota districts even if no apps yet
    for item in quotas:
        if item.district and item.district not in district_options:
            district_options.append(item.district)
    district_options = sorted(set(district_options))

    export_qs = _filter_query(
        admin_id=admin_id,
        q=q,
        program_id=program_id_val,
        school_name=school_name,
        district=district,
        enc=1 if enc_only else None,
        ppk=1 if ppk_only else None,
    )
    export_enc_qs = _filter_query(
        admin_id=admin_id,
        q=q,
        program_id=program_id_val,
        school_name=school_name,
        district=district,
        enc=1,
        ppk=1 if ppk_only else None,
    )
    return templates.TemplateResponse(
        "targeted_regional.html",
        _ctx(
            request,
            admin,
            quotas=quotas,
            progress=progress,
            dash=dash,
            applications=apps,
            programs=programs,
            enc_programs=enc_programs,
            programs_by_level=_programs_by_level(programs),
            school_options=school_options,
            district_options=district_options,
            materials=svc.available_materials(),
            filters={
                "q": q or "",
                "program_id": program_id_val or "",
                "school_name": school_name or "",
                "district": district or "",
                "enc": enc_only,
                "ppk": ppk_only,
            },
            export_url=f"/targeted/export.xlsx?{export_qs}",
            export_enc_url=f"/targeted/export.xlsx?{export_enc_qs}",
            app_is_enc=svc.application_is_enc,
        ),
    )


@router.get("/export.xlsx")
async def targeted_export(
    request: Request,
    admin_id: int,
    q: str = "",
    program_id: Optional[str] = None,
    school_name: str = "",
    district: str = "",
    enc: str = "",
    ppk: str = "",
    db: Session = Depends(get_db),
):
    admin = await run_in_threadpool(_user_by_id, db, admin_id)
    if not admin or not _is_admin(admin):
        return RedirectResponse("/login", status_code=303)

    year = svc.DEFAULT_YEAR
    program_id_val = _optional_int(program_id)
    enc_only = _truthy_flag(enc)
    ppk_only = _truthy_flag(ppk)

    if admin.role == "municipal_admin":
        apps = await run_in_threadpool(
            lambda: svc.list_applications(
                db,
                year,
                district=admin.district or "",
                program_id=program_id_val,
                school_name=school_name or None,
                q=q or None,
                enc_only=enc_only,
                ppk_only=ppk_only,
            )
        )
        tag = "enc_" if enc_only else ""
        filename = f"celevoe_{tag}{admin.district or 'rayon'}_{year}.xlsx"
    else:
        apps = await run_in_threadpool(
            lambda: svc.list_applications(
                db,
                year,
                region=admin.region,
                district=district or None,
                program_id=program_id_val,
                school_name=school_name or None,
                q=q or None,
                enc_only=enc_only,
                ppk_only=ppk_only,
            )
        )
        tag = "enc_" if enc_only else ""
        filename = f"celevoe_{tag}svod_{year}.xlsx"

    buf = await run_in_threadpool(svc.build_applications_workbook, apps, year)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/quotas", response_class=HTMLResponse)
async def targeted_quotas_page(
    request: Request,
    admin_id: int,
    saved: int = 0,
    db: Session = Depends(get_db),
):
    admin = await run_in_threadpool(_user_by_id, db, admin_id)
    if not admin or admin.role != "regional_admin":
        return RedirectResponse("/login", status_code=303)

    set_active_direction(request, DIRECTION_TARGETED)
    year = svc.DEFAULT_YEAR
    await run_in_threadpool(
        lambda: svc.ensure_quotas_seeded(
            db, year=year, region=(admin.region or "").strip() or "Чеченская Республика"
        )
    )
    quotas = await run_in_threadpool(lambda: svc.list_quotas(db, year))
    return templates.TemplateResponse(
        "targeted_quotas.html",
        _ctx(request, admin, quotas=quotas, saved=bool(saved)),
    )


@router.post("/quotas/save")
async def targeted_quotas_save(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        admin_id = int(form.get("admin_id"))
    except (TypeError, ValueError):
        return RedirectResponse("/login", status_code=303)

    admin = await run_in_threadpool(_user_by_id, db, admin_id)
    if not admin or admin.role != "regional_admin":
        return RedirectResponse("/login", status_code=303)

    try:
        year = int(form.get("year") or svc.DEFAULT_YEAR)
    except (TypeError, ValueError):
        year = svc.DEFAULT_YEAR

    # Не вызывать align перед сохранением: он не должен трогать id/значения формы.
    updated = await run_in_threadpool(
        lambda: svc.save_quotas_from_form(db, year=year, admin=admin, form_data=form)
    )
    return RedirectResponse(
        f"/targeted/quotas?admin_id={admin_id}&saved={1 if updated else 0}",
        status_code=303,
    )


@router.post("/applications/add")
async def targeted_application_add(
    request: Request,
    uid: int = Form(...),
    year: int = Form(svc.DEFAULT_YEAR),
    program_id: int = Form(...),
    applicants_count: int = Form(1),
    school_user_id: Optional[int] = Form(None),
    school_name: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    is_ppk: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    actor = await run_in_threadpool(_user_by_id, db, uid)
    if not actor:
        return RedirectResponse("/login", status_code=303)
    if actor.role not in ("municipal_admin", "regional_admin"):
        return RedirectResponse(f"/dashboard?uid={actor.id}", status_code=303)

    program = await run_in_threadpool(
        lambda: db.query(models.TargetProgram).filter(models.TargetProgram.id == program_id).first()
    )
    if not program:
        return RedirectResponse(home_for_user(actor, DIRECTION_TARGETED), status_code=303)

    school = None
    if school_user_id:
        school = await run_in_threadpool(_user_by_id, db, int(school_user_id))

    final_school_name = (
        (school.unit_name if school else None)
        or (school_name or "").strip()
        or "Без названия"
    )
    district = (school.district if school else None) or actor.district or ""
    region = (school.region if school else None) or actor.region
    ppk_flag = str(is_ppk or "").strip().lower() in ("1", "on", "true", "yes", "да")

    def _create():
        row = models.TargetApplication(
            year=year,
            region=region,
            district=district,
            school_user_id=school.id if school else None,
            school_name=final_school_name,
            program_id=program.id,
            program_label=svc.program_label(program),
            applicants_count=max(1, int(applicants_count or 1)),
            is_ppk=ppk_flag,
            note=(note or "").strip() or None,
            created_by=actor.id,
        )
        db.add(row)
        db.commit()

    await run_in_threadpool(_create)
    return RedirectResponse(home_for_user(actor, DIRECTION_TARGETED), status_code=303)


@router.post("/applications/delete")
async def targeted_application_delete(
    request: Request,
    uid: int = Form(...),
    app_id: int = Form(...),
    db: Session = Depends(get_db),
):
    actor = await run_in_threadpool(_user_by_id, db, uid)
    if not actor:
        return RedirectResponse("/login", status_code=303)
    if actor.role not in ("municipal_admin", "regional_admin"):
        return RedirectResponse(f"/dashboard?uid={actor.id}", status_code=303)

    def _delete():
        row = db.query(models.TargetApplication).filter(models.TargetApplication.id == app_id).first()
        if not row:
            return
        ok = False
        if actor.role == "municipal_admin" and row.district == actor.district:
            ok = True
        elif actor.role == "regional_admin":
            ok = True
        if ok:
            db.delete(row)
            db.commit()

    await run_in_threadpool(_delete)
    return RedirectResponse(home_for_user(actor, DIRECTION_TARGETED), status_code=303)


@router.get("/materials/{material_id}")
async def targeted_material_download(material_id: str):
    meta = next((m for m in svc.MATERIALS if m["id"] == material_id), None)
    if not meta:
        return RedirectResponse("/targeted", status_code=303)
    path = svc.material_path(meta["file"])
    if not path:
        return RedirectResponse("/targeted", status_code=303)
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(meta.get("kind"), "application/octet-stream")
    filename = quote(path.name)
    return FileResponse(
        path,
        media_type=media,
        filename=path.name,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
    )


@router.post("/switch")
async def switch_direction(
    request: Request,
    direction: str = Form(...),
    uid: Optional[int] = Form(None),
    admin_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user_id = uid or admin_id or request.session.get("user_id")
    user = await run_in_threadpool(_user_by_id, db, int(user_id)) if user_id else None
    if not user:
        return RedirectResponse("/login", status_code=303)

    # школы — только питание
    if user.role == "user":
        set_active_direction(request, DIRECTION_FOOD)
        return RedirectResponse(f"/dashboard?uid={user.id}", status_code=303)

    if user.role == "municipal_admin":
        d = normalize_direction(user.direction or DIRECTION_TARGETED)
    else:
        d = set_active_direction(request, direction)

    return RedirectResponse(home_for_user(user, d), status_code=303)
