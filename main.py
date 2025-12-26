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

# --- ФЕДЕРАЛЬНЫЙ МОНИТОРИНГ (РОБОТ) ---

from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import HTTPException

@app.get("/{uid}/food/", response_class=HTMLResponse)
async def federal_index(uid: int):
    """Выдаёт список всех файлов учреждения для федерального центра"""
    BASE_DIR = Path(__file__).resolve().parent
    base_path = BASE_DIR / str(uid) / "food"

    if not base_path.exists() or not any(base_path.iterdir()):
        return "<html><body><h1>Нет доступных файлов</h1></body></html>"

    links = "".join([
        f'<li><a href="{f.name}">{f.name}</a></li>'
        for f in sorted(base_path.iterdir(), reverse=True)
        if f.is_file()
    ])

    return f"""
    <html>
        <body>
            <h1>Index of /{uid}/food/</h1>
            <hr>
            <ul>{links}</ul>
        </body>
    </html>
    """

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
    import os

    user = db.query(models.User).get(uid)
    if not user:
        return RedirectResponse("/login")

    # Физический путь к папке food конкретной школы
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    food_path.mkdir(parents=True, exist_ok=True)

    # Список файлов из физической папки food
    files = os.listdir(food_path) if food_path.exists() else []

    # URL для мониторинга (оставляем как у тебя, чтобы шаблон работал)
    monitoring_url = f"{request.base_url}{uid}/food/"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "files": files,
        "period": f"{year}-{month}",
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
    from pathlib import Path
    import shutil

    # Путь к папке food учреждения
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    food_path.mkdir(parents=True, exist_ok=True)

    # Файлы сохраняем только сюда
    for f in files:
        if not f.filename:
            continue
        dest_path = food_path / f.filename
        f.file.seek(0)
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(f.file, buffer)

    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)

@app.get("/delete-file")
def delete_file(uid: int, year: str, month: str, filename: str):
    from pathlib import Path
    import os

    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / str(uid) / "food" / filename

    if file_path.exists():
        os.remove(file_path)

    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)
