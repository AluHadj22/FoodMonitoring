import os, shutil
from typing import List, Optional
from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

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

# --- ЛОГИКА ДЛЯ ФЕДЕРАЛЬНОГО МОНИТОРИНГА (РОБОТ) ---

@app.get("/{uid}/food/", response_class=HTMLResponse)
async def federal_index(uid: int):
    """Выдает список ВССЕХ файлов учреждения из всех папок месяцев (для робота)"""
    base_path = UPLOAD_DIR / str(uid)
    all_files = set()
    if base_path.exists():
        for month_dir in base_path.iterdir():
            food_path = month_dir / "food"
            if food_path.is_dir():
                for f in food_path.iterdir():
                    if f.is_file():
                        all_files.add(f.name)

    links = "".join([f'<li><a href="{n}">{n}</a></li>' for n in sorted(all_files, reverse=True)])
    return f"<html><body><h1>Index of /{uid}/food/</h1><hr><ul>{links}</ul></body></html>"

@app.get("/{uid}/food/{filename}")
async def get_federal_file(uid: int, filename: str):
    """Ищет и отдает файл, обходя все папки периодов"""
    base_path = UPLOAD_DIR / str(uid)
    if base_path.exists():
        for month_dir in base_path.iterdir():
            target = month_dir / "food" / filename
            if target.exists():
                return FileResponse(target)
    raise HTTPException(status_code=404, detail="Файл не найден")


# --- АВТОРИЗАЦИЯ И РЕГИСТРАЦИЯ ---

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
    # Определение роли по секретному коду
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


# --- ПАНЕЛЬ АДМИНИСТРАТОРА (ПОИСК И РАССЫЛКА) ---

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, admin_id: int, q: str = "", db: Session = Depends(get_db)):
    admin = db.query(models.User).get(admin_id)
    if not admin:
        return RedirectResponse("/login")

    # Базовая фильтрация по ролям
    query = db.query(models.User).filter(models.User.role == "user")

    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    # Поиск по названию учреждения
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
    admin = db.query(models.User).get(admin_id)
    period = f"{year}-{month}"

    # Фильтруем школы для рассылки
    query = db.query(models.User).filter(models.User.food_type == target_type, models.User.role == "user")
    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    targets = query.all()

    for school in targets:
        path = UPLOAD_DIR / str(school.id) / period / "food"
        path.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.filename:
                f.file.seek(0)
                with open(path / f.filename, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)

    return RedirectResponse(f"/admin?admin_id={admin_id}", status_code=303)


# --- ЛИЧНЫЙ КАБИНЕТ (ШКОЛА / УПРАВЛЕНИЕ) ---

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, uid: int, year: str = "2025", month: str = "05", db: Session = Depends(get_db)):
    user = db.query(models.User).get(uid)
    period = f"{year}-{month}"
    path = UPLOAD_DIR / str(uid) / period / "food"

    files = os.listdir(path) if path.exists() else []
    monitoring_url = f"{request.base_url}{uid}/food/"

    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "user": user, 
        "files": files, 
        "period": period, 
        "year": year, 
        "month": month, 
        "months": MONTHS, 
        "monitoring_url": monitoring_url
    })

@app.post("/upload")
async def upload_files(
    uid: int = Form(...), 
    year: str = Form(...), 
    month: str = Form(...), 
    files: List[UploadFile] = File(...)
):
    period = f"{year}-{month}"
    path = UPLOAD_DIR / str(uid) / period / "food"
    path.mkdir(parents=True, exist_ok=True)

    for f in files:
        if f.filename:
            with open(path / f.filename, "wb") as buffer:
                shutil.copyfileobj(f.file, buffer)

    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)

@app.get("/delete-file")
def delete_file(uid: int, year: str, month: str, filename: str):
    period = f"{year}-{month}"
    file_path = UPLOAD_DIR / str(uid) / period / "food" / filename
    if file_path.exists():
        os.remove(file_path)

    # Возврат в тот же период, где было удаление
    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)