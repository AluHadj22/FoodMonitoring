import os
import shutil
import json
import asyncio
import time
from typing import List, Optional, Dict
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from cachetools import TTLCache
import aiofiles
import aiofiles.os
from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor
import secrets
import hashlib

import models
import auth
from database import engine, get_db

# Глобальные кеши для высокой нагрузки
MANIFEST_CACHE = TTLCache(maxsize=5000, ttl=300)  # Кеш манифестов
USER_CACHE = TTLCache(maxsize=1000, ttl=180)      # Кеш пользователей
FILE_EXISTS_CACHE = TTLCache(maxsize=10000, ttl=60) # Кеш проверки файлов

# ThreadPool для блокирующих операций
IO_EXECUTOR = ThreadPoolExecutor(max_workers=50)

# Глобальная блокировка для кешей
CACHE_LOCK = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Запуск оптимизированного приложения...")
    yield
    print("🔧 Очистка ресурсов...")
    MANIFEST_CACHE.clear()
    USER_CACHE.clear()
    FILE_EXISTS_CACHE.clear()
    IO_EXECUTOR.shutdown()

app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=100)  # Сжатие для экономии трафика

templates = Jinja2Templates(directory="templates")

# Константы
FOOD_TYPES = ["Только завтраки", "Завтраки и обеды", "Интернаты", "Обеды"]
DISTRICTS = [
    "Аргун", "Ачхой-Мартановский", "Веденский", "Грозненский", "Грозный",
    "Гудермесский", "Гудермес", "Итум-Калинский", "Курчалоевский", "Надтеречный",
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

# Оптимизированные утилиты
async def run_in_threadpool(func, *args, **kwargs):
    """Запуск блокирующих операций в threadpool"""
    loop = asyncio.get_event_loop()
    if kwargs:
        # Если есть именованные аргументы, используем лямбду
        return await loop.run_in_executor(IO_EXECUTOR, lambda: func(*args, **kwargs))
    else:
        return await loop.run_in_executor(IO_EXECUTOR, func, *args)

def get_msk_time():
    return datetime.utcnow() + timedelta(hours=3)

async def get_cached_user(user_id: int, db: Session) -> Optional[models.User]:
    """Оптимизированное получение пользователя с кешированием"""
    cache_key = f"user_{user_id}"
    
    async with CACHE_LOCK:
        if cache_key in USER_CACHE:
            return USER_CACHE[cache_key]
        
        user = await run_in_threadpool(lambda: db.query(models.User).filter(models.User.id == user_id).first())
        if user:
            USER_CACHE[cache_key] = user
        return user

async def read_manifest_optimized(file_path: Path) -> dict:
    """Оптимизированное чтение manifest с кешированием"""
    cache_key = str(file_path)
    
    async with CACHE_LOCK:
        if cache_key in MANIFEST_CACHE:
            return MANIFEST_CACHE[cache_key].copy()
        
        manifest = {}
        exists = await run_in_threadpool(file_path.exists)
        
        if exists:
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    manifest = json.loads(content) if content else {}
            except Exception:
                pass
        
        MANIFEST_CACHE[cache_key] = manifest.copy()
        return manifest

async def write_manifest_optimized(file_path: Path, manifest: dict):
    """Оптимизированная запись manifest с обновлением кеша"""
    cache_key = str(file_path)
    
    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(manifest, ensure_ascii=False, indent=2))
    
    async with CACHE_LOCK:
        MANIFEST_CACHE[cache_key] = manifest.copy()

async def save_uploaded_file_optimized(file: UploadFile, dest_path: Path):
    """Оптимизированное сохранение файла"""
    content = await file.read()
    async with aiofiles.open(dest_path, "wb") as buffer:
        await buffer.write(content)
    
    # Обновляем кеш существования файла
    cache_key = str(dest_path)
    async with CACHE_LOCK:
        FILE_EXISTS_CACHE[cache_key] = True

async def delete_file_optimized(file_path: Path):
    """Оптимизированное удаление файла с очисткой кешей"""
    try:
        if await run_in_threadpool(file_path.exists):
            await run_in_threadpool(file_path.unlink)
            
            # Очищаем кеш существования файла
            cache_key = str(file_path)
            async with CACHE_LOCK:
                if cache_key in FILE_EXISTS_CACHE:
                    del FILE_EXISTS_CACHE[cache_key]
    except Exception:
        pass

async def list_directory_files_optimized(path: Path) -> List[Path]:
    """Асинхронное получение списка файлов в директории"""
    if not await run_in_threadpool(path.exists):
        return []
    
    try:
        items = await run_in_threadpool(lambda: list(path.iterdir()))
        files = []
        for item in items:
            if await run_in_threadpool(item.is_file):
                files.append(item)
        return files
    except OSError:
        return []

async def generate_federal_html_stream(uid: int, base_path: Path, manifest: dict):
    """Потоковая генерация HTML для федерального мониторинга"""
    yield f"""
    <html>
        <head>
            <meta charset="utf-8">
            <title>Файлы учреждения {uid}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
                .year-section {{ margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 6px; }}
                .month-section {{ margin: 10px 0; padding: 10px; background: #fff; border-left: 4px solid #3498db; }}
                .file-list {{ list-style: none; padding: 0; }}
                .file-item {{ padding: 8px 12px; margin: 5px 0; background: #f8f9fa; border-radius: 4px; display: flex; justify-content: space-between; align-items: center; }}
                .file-link {{ color: #2980b9; text-decoration: none; font-weight: bold; }}
                .file-link:hover {{ color: #1a5276; text-decoration: underline; }}
                .file-date {{ color: #7f8c8d; font-size: 0.9em; }}
                .no-files {{ color: #95a5a6; font-style: italic; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📁 Файлы учреждения {uid}</h1>
                <hr>
    """
    
    files = await list_directory_files_optimized(base_path)
    grouped_files = {}
    
    for f in files:
        if f.name == "manifest.json":
            continue
            
        file_meta = manifest.get(f.name, {})
        date_str = file_meta.get("upload_datetime", "")
        
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y %H:%M") if date_str else datetime.fromtimestamp(await run_in_threadpool(f.stat).st_mtime)
        except Exception:
            dt = datetime.now()
        
        assigned_year = file_meta.get("assigned_year", str(dt.year))
        assigned_month = file_meta.get("assigned_month", dt.strftime("%m"))
        month_name = MONTHS.get(assigned_month, assigned_month)

        grouped_files.setdefault(assigned_year, {}).setdefault(month_name, []).append({
            "filename": f.name,
            "date": dt.strftime("%d.%m.%Y %H:%M"),
            "size": await run_in_threadpool(f.stat).st_size
        })
    
    # Сортировка и потоковая выдача
    for year in sorted(grouped_files.keys(), reverse=True):
        yield f'<div class="year-section"><h2>📅 {year} год</h2>'
        
        for month in sorted(grouped_files[year].keys(), reverse=True):
            yield f'<div class="month-section"><h3>📊 {month}</h3><ul class="file-list">'
            
            for file_info in sorted(grouped_files[year][month], key=lambda x: x["date"], reverse=True):
                size_kb = file_info["size"] // 1024
                yield (
                    f'<li class="file-item">'
                    f'<a class="file-link" href="{file_info["filename"]}">📄 {file_info["filename"]}</a>'
                    f'<div><span class="file-date">{file_info["date"]}</span>'
                    f'<span style="margin-left: 15px; color: #27ae60;">{size_kb} KB</span></div>'
                    f'</li>'
                )
            
            yield '</ul></div>'
        yield '</div>'
    
    if not grouped_files:
        yield '<div class="no-files">📭 Нет доступных файлов</div>'
    
    yield '</div></body></html>'

# Middleware для мониторинга производительности
@app.middleware("http")
async def performance_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    if process_time > 1.0:  # Логируем только медленные запросы
        print(f"⏱️ SLOW_REQUEST: {request.method} {request.url} - {process_time:.3f}s")
    
    response.headers["X-Process-Time"] = f"{process_time:.3f}s"
    return response

# --- ФЕДЕРАЛЬНЫЙ МОНИТОРИНГ (ОПТИМИЗИРОВАННЫЙ) ---

@app.get("/{uid}/food/", response_class=HTMLResponse)
async def federal_index(uid: int):
    """Оптимизированная версия для федерального мониторинга"""
    BASE_DIR = Path(__file__).resolve().parent
    base_path = BASE_DIR / str(uid) / "food"

    # Быстрая проверка существования директории
    if not await run_in_threadpool(base_path.exists):
        return HTMLResponse(content="<html><body><h1>📭 Нет доступных файлов</h1></body></html>")

    # Кешированное чтение manifest
    manifest_path = base_path / "manifest.json"
    manifest = await read_manifest_optimized(manifest_path)

    # Потоковая генерация HTML
    return StreamingResponse(
        generate_federal_html_stream(uid, base_path, manifest),
        media_type="text/html"
    )

@app.get("/{uid}/food/{filename}")
async def get_federal_file(uid: int, filename: str):
    """Оптимизированная отдача файлов с кешированием"""
    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / str(uid) / "food" / filename

    # Кешированная проверка существования файла
    cache_key = str(file_path)
    async with CACHE_LOCK:
        if cache_key in FILE_EXISTS_CACHE:
            file_exists = FILE_EXISTS_CACHE[cache_key]
        else:
            file_exists = await run_in_threadpool(file_path.exists)
            FILE_EXISTS_CACHE[cache_key] = file_exists

    if file_exists:
        return FileResponse(
            file_path,
            filename=filename,
            headers={"Cache-Control": "public, max-age=3600"}
        )

    raise HTTPException(status_code=404, detail="Файл не найден")

# --- РЕГИСТРАЦИЯ И АВТОРИЗАЦИЯ (ОПТИМИЗИРОВАННЫЕ) ---

@app.get("/")
async def home():
    return RedirectResponse("/login")

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {
        "request": request,
        "districts": DISTRICTS,
        "food_types": FOOD_TYPES
    })

@app.post("/register")
async def register(
    email: str = Form(...),
    password: str = Form(...),
    unit_name: str = Form(...),
    district: str = Form(...),
    food_type: str = Form(...),
    secret_code: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # Проверка существования пользователя
    existing_user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )
    if existing_user:
        return "Пользователь с таким email уже существует"

    # Определение роли
    role = "user"
    if secret_code == REGIONAL_CODE:
        role = "regional_admin"
    elif secret_code == MUNICIPAL_CODE:
        role = "municipal_admin"

    # Создание пользователя
    hashed_pw = auth.get_password_hash(password)
    new_user = models.User(
        email=email,
        hashed_password=hashed_pw,
        unit_name=unit_name,
        district=district,
        food_type=food_type,
        role=role
    )
    
    await run_in_threadpool(lambda: db.add(new_user))
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, new_user)

    # Создание директорий
    BASE_DIR = Path(__file__).resolve().parent
    school_dir = BASE_DIR / str(new_user.id)
    food_dir = school_dir / "food"
    await run_in_threadpool(lambda: food_dir.mkdir(parents=True, exist_ok=True))

    return RedirectResponse("/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )
    
    if not user or not auth.verify_password(password, user.hashed_password):
        return "Неверный логин или пароль"

    if "admin" in user.role:
        return RedirectResponse(f"/admin?admin_id={user.id}", status_code=303)
    return RedirectResponse(f"/dashboard?uid={user.id}", status_code=303)

# --- АДМИН-ПАНЕЛЬ И РАССЫЛКА (ОПТИМИЗИРОВАННЫЕ) ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, admin_id: int, q: str = "", db: Session = Depends(get_db)):
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login")

    # Оптимизированный запрос с фильтрацией
    query = db.query(models.User).filter(models.User.role == "user")
    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    if q:
        query = query.filter(models.User.unit_name.ilike(f"%{q}%"))

    schools = await run_in_threadpool(query.all)

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
    request: Request,
    admin_id: int = Form(...),
    target_type: str = Form(...),
    year: str = Form(...),
    month: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    BASE_DIR = Path(__file__).resolve().parent
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login")

    # Фильтруем учреждения
    query = db.query(models.User).filter(
        models.User.food_type == target_type,
        models.User.role == "user"
    )
    if admin.role == "municipal_admin":
        query = query.filter(models.User.district == admin.district)

    schools = await run_in_threadpool(query.all)

    uploader_name = admin.unit_name if admin else f"ADMIN {admin_id}"
    uploader_ip = request.client.host if request.client else "—"

    # Асинхронная обработка для всех школ
    tasks = []
    for school in schools:
        food_path = BASE_DIR / str(school.id) / "food"
        await run_in_threadpool(lambda: food_path.mkdir(parents=True, exist_ok=True))
        manifest_path = food_path / "manifest.json"

        # Кешированное чтение manifest
        manifest = await read_manifest_optimized(manifest_path)

        # Параллельная обработка файлов
        for file in files:
            if not file.filename:
                continue

            dest_path = food_path / file.filename
            await save_uploaded_file_optimized(file, dest_path)

            # Обновление manifest
            manifest[file.filename] = {
                "assigned_year": year,
                "assigned_month": month,
                "uploader_name": uploader_name,
                "uploader_ip": uploader_ip,
                "upload_datetime": get_msk_time().strftime("%d.%m.%Y %H:%M")
            }

        # Кешированная запись manifest
        await write_manifest_optimized(manifest_path, manifest)

    return RedirectResponse(f"/admin?admin_id={admin_id}", status_code=303)

# --- ЛИЧНЫЙ КАБИНЕТ ШКОЛЫ (ОПТИМИЗИРОВАННЫЙ) ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    uid: int,
    year: str = "2025",
    month: str = "05",
    db: Session = Depends(get_db)
):
    user = await get_cached_user(uid, db)
    if not user:
        return RedirectResponse("/login")

    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    await run_in_threadpool(lambda: food_path.mkdir(parents=True, exist_ok=True))

    # Кешированное чтение manifest
    manifest_path = food_path / "manifest.json"
    manifest = await read_manifest_optimized(manifest_path)

    # Оптимизированное получение файлов
    files = await list_directory_files_optimized(food_path)
    grouped_files = {}

    for f in files:
        if f.name == "manifest.json":
            continue

        file_meta = manifest.get(f.name, {})
        upload_time = file_meta.get("upload_datetime", get_msk_time().strftime("%d.%m.%Y %H:%M"))

        assigned_year = file_meta.get("assigned_year", "2025")
        assigned_month = file_meta.get("assigned_month", "01")
        uploader_name = file_meta.get("uploader_name", user.unit_name)
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
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    await run_in_threadpool(lambda: food_path.mkdir(parents=True, exist_ok=True))

    manifest_path = food_path / "manifest.json"
    manifest = await read_manifest_optimized(manifest_path)

    user = await get_cached_user(uid, db)
    uploader_name = user.unit_name if user else f"UID {uid}"
    client_ip = request.client.host if request.client else "—"

    # Параллельная обработка файлов
    for file in files:
        if not file.filename:
            continue
        
        dest_path = food_path / file.filename
        await save_uploaded_file_optimized(file, dest_path)

        manifest[file.filename] = {
            "assigned_year": year,
            "assigned_month": month,
            "uploader_name": uploader_name,
            "uploader_ip": client_ip,
            "upload_datetime": get_msk_time().strftime("%d.%m.%Y %H:%M")
        }

    await write_manifest_optimized(manifest_path, manifest)

    return RedirectResponse(f"/dashboard?uid={uid}&year={year}&month={month}", status_code=303)

@app.get("/delete-file")
async def delete_file(uid: int, year: str, month: str, filename: str):
    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    file_path = food_path / filename
    manifest_path = food_path / "manifest.json"

    await delete_file_optimized(file_path)

    manifest = await read_manifest_optimized(manifest_path)
    if filename in manifest:
        del manifest[filename]
        await write_manifest_optimized(manifest_path, manifest)

    return RedirectResponse(
        f"/dashboard?uid={uid}&year={year}&month={month}",
        status_code=303
    )

@app.post("/delete-files")
async def delete_files(
    uid: int = Form(...),
    year: str = Form(...),
    month: str = Form(...),
    files: List[str] = Form(...)
):
    BASE_DIR = Path(__file__).resolve().parent
    folder = BASE_DIR / str(uid) / "food"
    manifest_path = folder / "manifest.json"

    manifest = await read_manifest_optimized(manifest_path)

    # Параллельное удаление файлов
    for filename in files:
        file_path = folder / filename
        await delete_file_optimized(file_path)
        manifest.pop(filename, None)

    await write_manifest_optimized(manifest_path, manifest)

    return RedirectResponse(
        f"/dashboard?uid={uid}&year={year}&month={month}",
        status_code=303
    )

# Эндпоинт для проверки здоровья
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": get_msk_time().isoformat(),
        "cache_stats": {
            "manifest_cache": len(MANIFEST_CACHE),
            "user_cache": len(USER_CACHE),
            "file_exists_cache": len(FILE_EXISTS_CACHE)
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        workers=4,
        loop="asyncio"
    )
