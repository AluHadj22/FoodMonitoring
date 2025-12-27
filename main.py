import os, shutil
from typing import List, Optional
from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from fastapi.responses import JSONResponse

import models, auth
from database import engine, get_db

# Инициализация БД и приложения
models.Base.metadata.create_all(bind=engine)
app = FastAPI()
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = Path("uploads")

# Константы и справочники
FOOD_TYPES = ["Только завтраки", "Завтраки и обеды", "Интернаты"]
DISTRICTS = [
    "Аргун", "Ачхой-Мартановский", "Веденский", "Грозненский", "Грозный",
    "Гудермесский", "Итум-Калинский", "Курчалоевский", "Надтеречный",
    "Наурский", "Ножай-Юртовский", "Серноводский", "Урус-Мартановский",
    "Шалинский", "Шаройский", "Шатойский", "Шелковской"
]
MONTHS = {
    "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
    "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
    "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
}

# Секретные коды для регистрации админов
REGIONAL_CODE = "alu1212993"
MUNICIPAL_CODE = "rayonadmin3377%"

# --- ФЕДЕРАЛЬНЫЙ МОНИТОРИНГ (РОБОТ) ---

from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import HTTPException

@app.get("/{uid}/food/", response_class=HTMLResponse)
async def federal_index(uid: int):
    """
    Показывает все файлы учреждения, сгруппированные по году и месяцу,
    но физически всё лежит в одной папке /{uid}/food.
    """
    from datetime import datetime

    BASE_DIR = Path(__file__).resolve().parent
    base_path = BASE_DIR / str(uid) / "food"

    if not base_path.exists() or not any(base_path.iterdir()):
        return "<html><body><h1>Нет доступных файлов</h1></body></html>"

    # Сбор всех файлов с датами
    grouped_files = {}
    for f in sorted(base_path.iterdir(), reverse=True):
        if not f.is_file():
            continue
        stat = f.stat()
        dt = datetime.fromtimestamp(stat.st_mtime)
        year = str(dt.year)
        month_num = dt.strftime("%m")
        month_name = MONTHS.get(month_num, month_num)

        grouped_files.setdefault(year, {}).setdefault(month_name, []).append({
            "filename": f.name,
            "date": dt.strftime("%d.%m.%Y %H:%M")
        })

    # Формируем HTML
    html = f"""
    <html>
        <head>
            <meta charset="utf-8">
            <title>Файлы учреждения {uid}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1, h2, h3 {{ color: #333; }}
                ul {{ list-style-type: none; padding-left: 20px; }}
                li {{ margin-bottom: 4px; }}
                a {{ text-decoration: none; color: #0066cc; }}
                a:hover {{ text-decoration: underline; }}
                .date {{ color: gray; font-size: 0.9em; margin-left: 8px; }}
            </style>
        </head>
        <body>
            <h1>Файлы учреждения {uid}</h1>
            <hr>
    """

    for year in sorted(grouped_files.keys(), reverse=True):
        html += f"<h2>{year}</h2>"
        for month in sorted(grouped_files[year].keys(), reverse=True):
            html += f"<h3>{month}</h3><ul>"
            for file_info in grouped_files[year][month]:
                html += (
                    f'<li><a href="{file_info["filename"]}">'
                    f'{file_info["filename"]}</a>'
                    f'<span class="date">{file_info["date"]}</span></li>'
                )
            html += "</ul>"
    html += "</body></html>"

    return HTMLResponse(content=html)

@app.get("/{uid}/food/{filename}")
async def get_federal_file(uid: int, filename: str):
    """Отдаёт файл федеральному центру"""
    from pathlib import Path
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / str(uid) / "food" / filename

    if file_path.exists():
        return FileResponse(file_path, filename=filename)

    raise HTTPException(status_code=404, detail="Файл не найден")

# --- РЕГИСТРАЦИЯ И АВТОРИЗАЦИЯ ---

@app.get("/")
def home():
    return RedirectResponse("/login")

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {
        "request": request,
        "districts": DISTRICTS,
        "food_types": FOOD_TYPES
    })

@app.post("/register")
def register(
    email: str = Form(...),
    password: str = Form(...),
    unit_name: str = Form(...),
    district: str = Form(...),
    food_type: str = Form(...),
    secret_code: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    role = "user"
    if secret_code == REGIONAL_CODE:
        role = "regional_admin"
    elif secret_code == MUNICIPAL_CODE:
        role = "municipal_admin"

    hashed_pw = auth.get_password_hash(password)
    new_user = models.User(
        email=email,
        hashed_password=hashed_pw,
        unit_name=unit_name,
        district=district,
        food_type=food_type,
        role=role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # --- Создание структуры папок в корне проекта ---
    from pathlib import Path
    BASE_DIR = Path(__file__).resolve().parent
    school_dir = BASE_DIR / str(new_user.id)
    food_dir = school_dir / "food"
    food_dir.mkdir(parents=True, exist_ok=True)
    # -----------------------------------------------

    return RedirectResponse("/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.verify_password(password, user.hashed_password):
        return "Неверный логин или пароль"

    if "admin" in user.role:
        return RedirectResponse(f"/admin?admin_id={user.id}", status_code=303)
    return RedirectResponse(f"/dashboard?uid={user.id}", status_code=303)

# --- АДМИН-ПАНЕЛЬ И РАССЫЛКА ---

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, admin_id: int, q: str = "", db: Session = Depends(get_db)):
    admin = db.query(models.User).get(admin_id)
    if not admin:
        return RedirectResponse("/login")

    query = db.query(models.User).filter(models.User.role == "user")
    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    if q:
        query = query.filter(models.User.unit_name.ilike(f"%{q}%"))

    schools = query.all()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "admin": admin,
        "schools": schools,
        "food_types": FOOD_TYPES,
        "months": MONTHS,
        "search_query": q
    })

@app.post("/bulk-upload")
async def bulk_upload(
    admin_id: int = Form(...),
    target_type: str = Form(...),
    year: str = Form(...),
    month: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    from pathlib import Path
    import shutil

    admin = db.query(models.User).get(admin_id)
    period = f"{year}-{month}"

    query = db.query(models.User).filter(models.User.food_type == target_type, models.User.role == "user")
    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    targets = query.all()

    BASE_DIR = Path(__file__).resolve().parent

    for school in targets:
        # Путь к папке food каждой школы
        food_path = BASE_DIR / str(school.id) / "food"
        food_path.mkdir(parents=True, exist_ok=True)

        # Копируем каждый файл, отправленный администратором, в папку food школы
        for f in files:
            if not f.filename:
                continue
            dest_path = food_path / f.filename
            f.file.seek(0)
            with open(dest_path, "wb") as buffer:
                shutil.copyfileobj(f.file, buffer)

    return RedirectResponse(f"/admin?admin_id={admin_id}", status_code=303)

# --- ЛИЧНЫЙ КАБИНЕТ ШКОЛЫ ---

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    uid: int,
    year: str = "2025",
    month: str = "05",
    db: Session = Depends(get_db)
):
    from pathlib import Path
    import os, json
    from datetime import datetime

    user = db.query(models.User).get(uid)
    if not user:
        return RedirectResponse("/login")

    # --- Путь к папке учреждения ---
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    food_path.mkdir(parents=True, exist_ok=True)

    # --- Загружаем manifest.json, если он есть ---
    manifest_path = food_path / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
        except Exception:
            manifest = {}

    # --- Формируем группировку по назначенным периодам ---
    grouped_files = {}

    for f in sorted(food_path.iterdir(), reverse=True):
        if not f.is_file() or f.name == "manifest.json":
            continue

        stat = f.stat()
        dt = datetime.fromtimestamp(stat.st_mtime)

        file_meta = manifest.get(f.name, {})

        # Берём назначенные значения, если они есть
        assigned_year = file_meta.get("assigned_year", str(dt.year))
        assigned_month = file_meta.get("assigned_month", dt.strftime("%m"))
        uploader_name = file_meta.get("uploader_name", user.unit_name)
        upload_time = file_meta.get("upload_datetime", dt.strftime("%d.%m.%Y %H:%M"))
        uploader_ip = file_meta.get("uploader_ip", "—")
        month_name = MONTHS.get(assigned_month, assigned_month)

        grouped_files.setdefault(assigned_year, {}).setdefault(month_name, []).append({
            "filename": f.name,
            "date": upload_time,
            "uploader": uploader_name,
            "ip": uploader_ip,
        })

    monitoring_url = f"{request.base_url}{uid}/food/"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "files_grouped": grouped_files,
        "period": f"{year}-{month}",
        "year": year,
        "month": month,
        "months": MONTHS,
        "monitoring_url": monitoring_url
    })


@app.post("/upload")
async def upload_files(
    request: Request,
    uid: int = Form(...),
    year: str = Form(...),
    month: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    from pathlib import Path
    import shutil, json
    from datetime import datetime

    # Путь к папке food учреждения
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    food_path.mkdir(parents=True, exist_ok=True)

    # Путь к manifest.json
    manifest_path = food_path / "manifest.json"

    # Загружаем существующий manifest
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
        except Exception:
            manifest = {}
    else:
        manifest = {}

    # Получаем данные пользователя и IP
    user = db.query(models.User).get(uid)
    uploader_name = user.unit_name if user else f"UID {uid}"
    client_ip = request.client.host if request.client else "—"

    # Обработка файлов
    for f in files:
        if not f.filename:
            continue
        dest_path = food_path / f.filename
        f.file.seek(0)
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(f.file, buffer)

        # Запись метаданных
        manifest[f.filename] = {
            "assigned_year": year,
            "assigned_month": month,
            "uploader_name": uploader_name,
            "uploader_ip": client_ip,
            "upload_datetime": datetime.now().strftime("%d.%m.%Y %H:%M")
        }

    # Сохраняем обновлённый manifest.json
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)

    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)


@app.get("/delete-file")
def delete_file(uid: int, year: str, month: str, filename: str):
    from pathlib import Path
    import os, json

    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    file_path = food_path / filename
    manifest_path = food_path / "manifest.json"

    # Удаляем сам файл
    if file_path.exists():
        os.remove(file_path)

    # Чистим запись в manifest.json
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
        except Exception:
            manifest = {}
        if filename in manifest:
            del manifest[filename]
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, ensure_ascii=False, indent=2)

    # 👇 Возвращаем редирект на тот же период
    return RedirectResponse(
        f"/dashboard?uid={uid}&year={year}&month={month}",
        status_code=303
    )