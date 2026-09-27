# Стандартные библиотеки Python
import os
import tempfile
import re
import json
import asyncio
import time
import secrets
import hashlib
import urllib.parse
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Optional, Dict
import calendar as pycal
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse  # Уже есть, не меняем
from fastapi.middleware.cors import CORSMiddleware  # Добавить, если нет

# Файловый ввод‑вывод
import aiofiles
import aiofiles.os
import shutil

# Веб‑фреймворк и HTTP
from fastapi import (
    FastAPI,
    Request,
    Form,
    File,
    UploadFile,
    Depends,
    HTTPException,
    Response
)
from fastapi.responses import (
    HTMLResponse,
    FileResponse,
    StreamingResponse,
    RedirectResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware

# База данных
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, and_
from database import engine, Base, get_db
import models
from models import User
import fcmp_service
import dashboard_service

# Аутентификация и безопасность
from jose import JWTError, jwt
import auth

# Почта и SMTP
import aiosmtplib
from email.mime.text import MIMEText
from email.headerregistry import Address

# Работа с Excel
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

# Кеширование
from cachetools import TTLCache

# Асинхронные инструменты
from concurrent.futures import ThreadPoolExecutor

# Переменные окружения
from dotenv import load_dotenv

# Сессии
from starlette.middleware.sessions import SessionMiddleware

import logging

from fastapi.staticfiles import StaticFiles

# Импортируем отдельную БД для библиотеки знаний
from knowledge_base_db import (
    get_kb_db,
    KnowledgeBaseCategory,
    KnowledgeBaseDocument,
    KnowledgeBaseFavorite,
    KnowledgeBaseComment,
    KnowledgeBaseSearchLog,
    KnowledgeBaseAdmin,
    init_kb_db  # Правильное название функции
)
import kb_ai_service

# НОВЫЙ ИМПОРТ для оптимизации изображений
try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("⚠️  PIL не установлен. Оптимизация изображений отключена. Установите: pip install Pillow")

# Инициализируем БД библиотеки знаний при запуске
init_kb_db()

# Загрузка переменных из .env (абсолютный путь — для systemd)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# Создаёт таблицы в БД, если их нет
Base.metadata.create_all(bind=engine)

# Глобальные кеши для высокой нагрузки
MANIFEST_CACHE = TTLCache(maxsize=5000, ttl=300)  # Кеш манифестов
USER_CACHE = TTLCache(maxsize=1000, ttl=180)  # Кеш пользователей
FILE_EXISTS_CACHE = TTLCache(maxsize=10000, ttl=60)  # Кеш проверки файлов
IMAGE_RESPONSE_CACHE = TTLCache(maxsize=200, ttl=3600)  # НОВЫЙ: Кеш для изображений (1 час)
RESET_ATTEMPTS_CACHE = TTLCache(maxsize=1000, ttl=300)  # 5 минут
KB_AI_RATE_CACHE = TTLCache(maxsize=2000, ttl=600)  # лимит запросов к ИИ

# ThreadPool для блокирующих операций
IO_EXECUTOR = ThreadPoolExecutor(max_workers=50)
IMAGE_EXECUTOR = ThreadPoolExecutor(max_workers=4)  # НОВЫЙ: Отдельный пул для изображений

# Глобальная блокировка для кешей
CACHE_LOCK = asyncio.Lock()

# Константа для доступа к админке дашбордов
DASHBOARD_ADMIN_CODE = "admin3377%"

# НОВЫЕ КОНСТАНТЫ для оптимизации изображений
THUMBNAIL_SIZES = {
    'small': (150, 150),  # Для превью
    'medium': (400, 400),  # Для списков
    'large': (800, 800),  # Для просмотра
}
JPEG_QUALITY = 85
PNG_COMPRESSION = 6
MAX_IMAGE_SIZE = (1200, 1200)  # Максимальный размер изображения


# ВАЖНО: ОПРЕДЕЛЯЕМ run_in_threadpool РАНЬШЕ, ЧТОБЫ ОНА БЫЛА ДОСТУПНА ВСЕМ
async def run_in_threadpool(func, *args, **kwargs):
    """Запуск блокирующих операций в threadpool"""
    loop = asyncio.get_event_loop()
    if kwargs:
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

    cache_key = str(dest_path)
    async with CACHE_LOCK:
        FILE_EXISTS_CACHE[cache_key] = True


async def delete_file_optimized(file_path: Path):
    """Оптимизированное удаление файла с очисткой кешей"""
    try:
        if await run_in_threadpool(file_path.exists):
            await run_in_threadpool(file_path.unlink)

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


# НОВЫЕ ФУНКЦИИ ДЛЯ ОПТИМИЗАЦИИ ИЗОБРАЖЕНИЙ
async def optimize_image_async(
        input_path: Path,
        output_path: Path = None,
        max_size: tuple = MAX_IMAGE_SIZE,
        quality: int = JPEG_QUALITY
):
    """
    Асинхронная оптимизация изображения
    """
    if not HAS_PIL:
        # Если PIL не установлен, просто копируем файл
        if output_path and output_path != input_path:
            await asyncio.to_thread(shutil.copy2, input_path, output_path)
        return {
            'original_size': input_path.stat().st_size,
            'new_size': input_path.stat().st_size,
            'saved_percent': 0,
            'output_path': output_path or input_path
        }

    if output_path is None:
        output_path = input_path.parent / f"optimized_{input_path.name}"

    loop = asyncio.get_event_loop()

    def _optimize():
        try:
            # Открываем изображение
            with Image.open(input_path) as img:
                # Конвертируем в RGB если нужно
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')

                # Изменяем размер, сохраняя пропорции
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                # Определяем формат и сохраняем с оптимизацией
                format = 'JPEG' if input_path.suffix.lower() in ['.jpg', '.jpeg'] else 'PNG'

                save_kwargs = {
                    'format': format,
                    'optimize': True
                }

                if format == 'JPEG':
                    save_kwargs['quality'] = quality
                    save_kwargs['progressive'] = True
                else:
                    save_kwargs['compress_level'] = PNG_COMPRESSION

                img.save(output_path, **save_kwargs)

                original_size = input_path.stat().st_size
                new_size = output_path.stat().st_size

                return {
                    'original_size': original_size,
                    'new_size': new_size,
                    'saved_percent': (1 - new_size / original_size) * 100 if original_size > 0 else 0,
                    'output_path': output_path
                }
        except Exception as e:
            logger.error(f"Ошибка оптимизации {input_path}: {e}")
            # В случае ошибки копируем оригинал
            if output_path != input_path:
                shutil.copy2(input_path, output_path)
            return {
                'original_size': input_path.stat().st_size,
                'new_size': input_path.stat().st_size,
                'saved_percent': 0,
                'output_path': output_path
            }

    return await loop.run_in_executor(IMAGE_EXECUTOR, _optimize)


async def get_thumbnail_path(original_path: Path, size: str = 'medium') -> Path:
    """
    Получение пути к уменьшенной версии изображения
    """
    thumb_dir = original_path.parent / 'thumbnails'
    thumb_dir.mkdir(exist_ok=True)

    stem = original_path.stem
    ext = original_path.suffix

    thumbnail_path = thumb_dir / f"{stem}_{size}{ext}"

    # Если уменьшенная версия не существует или оригинал новее - создаем
    if not thumbnail_path.exists() or (
            original_path.stat().st_mtime > thumbnail_path.stat().st_mtime
    ):
        dimensions = THUMBNAIL_SIZES.get(size, THUMBNAIL_SIZES['medium'])
        await optimize_image_async(
            original_path,
            output_path=thumbnail_path,
            max_size=dimensions,
            quality=75 if size == 'small' else 85
        )

    return thumbnail_path


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Запуск оптимизированного приложения...")
    yield
    print("🔧 Очистка ресурсов...")
    MANIFEST_CACHE.clear()
    USER_CACHE.clear()
    FILE_EXISTS_CACHE.clear()
    IMAGE_RESPONSE_CACHE.clear()
    IO_EXECUTOR.shutdown()
    IMAGE_EXECUTOR.shutdown()  # НОВЫЙ: Очистка пула для изображений


app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)  # Увеличен minimum_size для сжатия

# НОВЫЙ: Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Добавляем middleware для сессий (ВАЖНО: после GZipMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "your-very-secret-key-change-in-production-12345")
)


# НОВЫЙ: Класс для статических файлов с кешированием
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            # Добавляем заголовки кеширования
            response.headers["Cache-Control"] = "public, max-age=3600"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


# Подключаем папку static/ с кешированием
app.mount("/static", CachedStaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

BASE_DIR = Path(__file__).resolve().parent


@app.get("/manifest.webmanifest")
async def pwa_manifest():
    path = BASE_DIR / "static" / "manifest.webmanifest"
    return FileResponse(
        path,
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sw.js")
async def pwa_service_worker():
    """SW must be served from site root for full scope '/'."""
    path = BASE_DIR / "static" / "sw.js"
    return FileResponse(
        path,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/offline", response_class=HTMLResponse)
async def offline_page(request: Request):
    return templates.TemplateResponse("offline.html", {"request": request})


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    host = request.headers.get("host") or request.url.hostname or "localhost"
    scheme = request.url.scheme
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /dashboard-admin\n"
        "Disallow: /regional-admin\n"
        "Disallow: /knowledge-base/admin\n"
        f"Sitemap: {scheme}://{host}/sitemap.xml\n"
    )


@app.get("/sitemap.xml", response_class=Response)
async def sitemap_xml(request: Request):
    host = request.headers.get("host") or request.url.hostname or "localhost"
    scheme = request.url.scheme
    base = f"{scheme}://{host}"
    paths = [
        "/",
        "/login",
        "/register",
        "/support",
        "/tutorials",
        "/knowledge-base",
        "/analis",
        "/util",
        "/privacy.html",
        "/agree.html",
        "/oferta.html",
        "/appeal",
    ]
    urls = "\n".join(
        f"  <url><loc>{base}{p}</loc><changefreq>weekly</changefreq></url>"
        for p in paths
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml; charset=utf-8")


# ДЛЯ ПЕРСОНАЛЬНЫХ ДАННЫХ
@app.get("/privacy.html", response_class=HTMLResponse)
async def get_privacy(request: Request):
    try:
        return templates.TemplateResponse("privacy.html", {"request": request})
    except Exception:
        raise HTTPException(status_code=404, detail="Документ не найден")


@app.get("/agree.html", response_class=HTMLResponse)
async def get_agree(request: Request):
    try:
        return templates.TemplateResponse("agree.html", {"request": request})
    except Exception:
        raise HTTPException(status_code=404, detail="Документ не найден")


@app.get("/oferta.html", response_class=HTMLResponse)
async def get_oferta(request: Request):
    try:
        return templates.TemplateResponse("oferta.html", {"request": request})
    except Exception:
        raise HTTPException(status_code=404, detail="Документ не найден")


@app.get("/pay.html", response_class=HTMLResponse)
async def get_pay(request: Request):
    try:
        return templates.TemplateResponse("pay.html", {"request": request})
    except Exception:
        raise HTTPException(status_code=404, detail="Документ не найден")


# --- СТРАНИЦА АНАЛИЗА СТАТИСТИКИ ---
@app.get("/analis", response_class=HTMLResponse)
async def analis_page(request: Request):
    """Страница анализа статистики"""
    return templates.TemplateResponse("analis.html", {"request": request})


# ========== СТРАНИЦА УТИЛИТ ЕЦМП ==========
@app.get("/util", response_class=HTMLResponse)
async def utilities_page(request: Request):
    """Страница с утилитами ЕЦМП"""
    return templates.TemplateResponse("util.html", {"request": request})


@app.get("/appeal", response_class=HTMLResponse)
async def appeal_page(request: Request):
    """Обращение разработчика к пользователям"""
    return templates.TemplateResponse("appeal.html", {"request": request})


# ========== НОВЫЕ СТРАНИЦЫ ==========

# Страница ФЦМПО
@app.get("/fcmp-support", response_class=HTMLResponse)
async def fcmp_support(request: Request):
    """Страница базы данных ФЦМПО"""
    # Проверяем, является ли пользователь админом
    is_admin = request.session.get("knowledge_base_admin", False)

    # Загружаем заявки из JSON файла
    import json
    requests_file = Path(__file__).resolve().parent / "data" / "fcmp_requests.json"

    requests = []
    if requests_file.exists():
        async with aiofiles.open(requests_file, "r", encoding="utf-8") as f:
            content = await f.read()
            if content:
                requests = json.loads(content)

    return templates.TemplateResponse("fcmp_support.html", {
        "request": request,
        "is_admin": is_admin,
        "requests": requests
    })


# Страница техподдержки
@app.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    """Страница технической поддержки"""
    return templates.TemplateResponse("support.html", {"request": request})


# Страница инструктаж
@app.get("/tutorials", response_class=HTMLResponse)
async def tutorials_page(request: Request):
    """Страница с видеоинструкциями"""
    return templates.TemplateResponse("tutorials.html", {"request": request})


# API для работы с заявками (сохранение в JSON)
@app.post("/api/fcmp-request")
async def save_fcmp_request(request: Request):
    """Сохранение заявки в JSON файл"""
    import json
    data = await request.json()

    requests_file = Path(__file__).resolve().parent / "data" / "fcmp_requests.json"
    requests_file.parent.mkdir(exist_ok=True)

    # Загружаем существующие заявки
    requests = []
    if requests_file.exists():
        async with aiofiles.open(requests_file, "r", encoding="utf-8") as f:
            content = await f.read()
            if content:
                requests = json.loads(content)

    # Добавляем новую заявку
    new_request = {
        "id": len(requests) + 1,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "school": data.get("school"),
        "email": data.get("email"),
        "problem": data.get("problem"),
        "status": "pending",
        "reply": None
    }
    requests.append(new_request)

    # Сохраняем
    async with aiofiles.open(requests_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(requests, ensure_ascii=False, indent=2))

    return {"status": "success", "id": new_request["id"]}


# Константы
FOOD_TYPES = ["Только завтраки", "Завтраки и обеды", "Интернаты", "Обеды"]
DISTRICTS = [
    "Аргун", "Ачхой-Мартановский", "Веденский", "Грозненский", "Грозный",
    "Гудермесский", "Гудермес", "Итум-Калинский", "Курчалоевский", "Надтеречный",
    "Наурский", "Ножай-Юртовский", "Серноводский", "Урус-Мартановский",
    "Шалинский", "Шаройский", "Шатойский", "Шелковской", "ГБОУ"
]
MONTHS = {
    "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
    "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
    "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
}

# Секретные коды для регистрации админов
REGIONAL_CODE = "alu1212993"
MUNICIPAL_CODE = "rayonadmin3377%"
RESET_SECRET_CODE = "9f#G7$kL2!pQ4@mZ8?xR5"  # Сложный код

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _norm_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _is_chechnya_region(region: Optional[str]) -> bool:
    region_l = _norm_text(region).lower()
    return any(token in region_l for token in ("чечен", "чечня", "грозн"))


def get_districts_for_regional_admin(db: Session, admin: models.User) -> list:
    """Районы, видимые региональному админу (включая школы с «битым» region)."""
    districts = set()

    rows = db.query(models.User.district).filter(
        models.User.region == admin.region,
        models.User.district.isnot(None),
        models.User.district != "",
    ).distinct().all()
    districts.update(_norm_text(r[0]) for r in rows if r[0])

    mun_rows = db.query(models.User.district).filter(
        models.User.role == "municipal_admin",
        models.User.region == admin.region,
        models.User.district.isnot(None),
        models.User.district != "",
    ).distinct().all()
    districts.update(_norm_text(r[0]) for r in mun_rows if r[0])

    # Регион вводится вручную → школы часто без/с другим region; муниципальный
    # их видит по district. Для ЧР подключаем справочник районов.
    if _is_chechnya_region(admin.region):
        districts.update(DISTRICTS)

    return sorted(d for d in districts if d)


def apply_admin_school_scope(query, admin: models.User, db: Session):
    """Ограничение списка школ по роли админа (единая логика для /admin и bulk)."""
    if admin.role == "municipal_admin":
        admin_district = _norm_text(admin.district)
        return query.filter(func.trim(models.User.district) == admin_district)

    if admin.role == "regional_admin":
        region_districts = get_districts_for_regional_admin(db, admin)
        scope_filters = [models.User.region == admin.region]
        if region_districts:
            # Школы района, которые видит муниципальный админ, даже если region не совпал
            scope_filters.append(func.trim(models.User.district).in_(region_districts))
        return query.filter(or_(*scope_filters))

    # Неизвестная роль — ничего не отдаём
    return query.filter(models.User.id == -1)


## ИСПРАВЛЕННАЯ ФУНКЦИЯ update_excel_content (обновляет содержимое в файлах)
async def update_excel_content(
        file_path: Path,
        school_name: str,
        director_name: str,
        year: str,
        date_str: str = None  # Ожидаем дату в формате ДД.ММ.ГГГГ
):
    temp_path = None

    try:
        temp_dir = file_path.parent
        temp_name = f"{file_path.stem}_temp_{os.getpid()}_{id(file_path)}{file_path.suffix}"
        temp_path = temp_dir / temp_name

        await asyncio.to_thread(shutil.copy2, file_path, temp_path)

        wb = load_workbook(temp_path)

        for sheet in wb.worksheets:
            if file_path.name.startswith("tm") and file_path.name.endswith(".xlsx"):
                # tm файлы: название школы в C1, ФИО директора в H2
                if sheet["C1"].value is not None:
                    sheet["C1"] = school_name
                if sheet["H2"].value is not None:
                    sheet["H2"] = director_name

            elif file_path.name.startswith("kp") and file_path.name.endswith(".xlsx"):
                # kp файлы: название школы в B1, год в AD1
                if sheet["B1"].value is not None:
                    sheet["B1"] = school_name
                if sheet["AD1"].value is not None:
                    sheet["AD1"] = year

            else:
                # Обычные файлы меню: название школы в B1, дата в J1
                if sheet["B1"].value is not None:
                    sheet["B1"] = school_name
                if sheet["J1"].value is not None and date_str:
                    # Используем дату из названия файла
                    sheet["J1"] = date_str

        wb.save(temp_path)
        wb.close()

        await asyncio.to_thread(shutil.move, temp_path, file_path)

    except Exception as e:
        print(f"Ошибка при обновлении {file_path}: {e}")
        if temp_path and temp_path.exists():
            try:
                await asyncio.to_thread(temp_path.unlink)
            except Exception as del_err:
                print(f"Не удалось удалить временный файл {temp_path}: {del_err}")
        raise

    finally:
        if temp_path and temp_path.exists():
            try:
                await asyncio.to_thread(temp_path.unlink)
            except:
                pass


# Удаляем дубликат run_in_threadpool, так как он уже определен выше

def _format_file_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} Б"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f} КБ" if kb >= 10 else f"{kb:.1f} КБ".replace(".0", "")
    return f"{kb / 1024:.1f} МБ"


async def build_public_menu_context(
        uid: int,
        base_path: Path,
        manifest: dict,
        school_name: str,
        filter_year: Optional[str] = None,
        filter_month: Optional[str] = None
) -> dict:
    """Данные для публичной страницы ежедневного меню."""

    files = await list_directory_files_optimized(base_path)

    def get_file_date(filename: str):
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
        if match:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return datetime(2000, 1, 1)

    files_sorted = sorted(
        [f for f in files if f.name != "manifest.json"],
        key=lambda f: get_file_date(f.name),
        reverse=True,
    )

    grouped_all: dict = {}
    special_files = {"tm": [], "kp": [], "findex": []}

    for f in files_sorted:
        file_meta = manifest.get(f.name, {})
        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', f.name)
        if not date_match and f.name != "findex.xlsx":
            continue

        size = f.stat().st_size
        size_label = _format_file_size(size)

        if f.name == "findex.xlsx":
            special_files["findex"].append({
                "filename": f.name,
                "date": file_meta.get("upload_datetime", "—"),
                "size": size,
                "size_label": size_label,
            })
            continue

        year, month, day = date_match.groups()
        item = {
            "filename": f.name,
            "day": day,
            "month": month,
            "year": year,
            "date": f"{day}.{month}.{year}",
            "size": size,
            "size_label": size_label,
        }

        name_l = f.name.lower()
        if name_l.startswith("tm") and name_l.endswith("-sm.xlsx"):
            special_files["tm"].append(item)
        elif name_l.startswith("kp") and name_l.endswith(".xlsx"):
            special_files["kp"].append(item)
        else:
            grouped_all.setdefault(year, {}).setdefault(month, []).append(item)

    available_years = sorted(grouped_all.keys(), reverse=True)

    # Меню на сегодня — из полного архива, не из фильтра
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    today_file = None
    all_menu = []
    for year_data in grouped_all.values():
        for month_files in year_data.values():
            all_menu.extend(month_files)
    for file_info in all_menu:
        if file_info["filename"].startswith(today_str):
            today_file = file_info
            break
    if not today_file and all_menu:
        all_menu_sorted = sorted(
            all_menu,
            key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y"),
            reverse=True,
        )
        today_file = all_menu_sorted[0]

    grouped = grouped_all
    if filter_year:
        grouped = {filter_year: grouped_all[filter_year]} if filter_year in grouped_all else {}
    if filter_month and filter_year and filter_year in grouped:
        month_files = grouped[filter_year].get(filter_month, [])
        grouped = {filter_year: {filter_month: month_files}} if month_files else {}

    years = []
    for year in sorted(grouped.keys(), reverse=True):
        months = []
        for mid in sorted(grouped[year].keys(), reverse=True):
            files_list = sorted(grouped[year][mid], key=lambda x: int(x["day"]))
            months.append({
                "id": mid,
                "name": MONTHS.get(mid, mid),
                "files": files_list,
            })
        year_total = sum(len(m["files"]) for m in months)
        years.append({"year": year, "total": year_total, "months": months})

    total_files_count = sum(y["total"] for y in years)
    years_count = len(available_years)
    # русское склонение «год/года/лет»
    n = years_count % 100
    n1 = years_count % 10
    if 11 <= n <= 14:
        years_label = "лет"
    elif n1 == 1:
        years_label = "год"
    elif 2 <= n1 <= 4:
        years_label = "года"
    else:
        years_label = "лет"

    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    special_files["tm"] = sorted(special_files["tm"], key=lambda x: x["year"], reverse=True)
    special_files["kp"] = sorted(special_files["kp"], key=lambda x: x["year"], reverse=True)

    if not available_years and filter_year is None:
        available_years = [str(today.year)]

    return {
        "uid": uid,
        "school_name": school_name,
        "total_files_count": total_files_count,
        "years_count": years_count,
        "years_label": years_label,
        "today_file": today_file,
        "today_date": today.strftime("%d.%m.%Y"),
        "today_weekday": weekdays[today.weekday()],
        "years": years,
        "special_files": special_files,
        "available_years": available_years,
        "filter_year": filter_year or "",
        "filter_month": filter_month or "",
        "months_items": list(MONTHS.items()),
    }


# НОВЫЙ МИДЛВАР ДЛЯ КЕШИРОВАНИЯ ИЗОБРАЖЕНИЙ
@app.middleware("http")
async def cache_images_middleware(request: Request, call_next):
    """Middleware для кеширования изображений"""

    # НЕ кешируем статические файлы сайта
    if request.url.path.startswith('/static/'):
        response = await call_next(request)
        # Добавляем простой кеш для статики
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    # Для аватаров и файлов питания применяем кеширование
    if request.url.path.startswith(('/avatar/', '/food/')):
        # Проверяем заголовки кеширования
        if_none_match = request.headers.get('if-none-match')
        cache_key = f"img_{request.url.path}"

        if cache_key in IMAGE_RESPONSE_CACHE:
            cached = IMAGE_RESPONSE_CACHE[cache_key]
            if if_none_match and if_none_match == cached.get('etag'):
                return Response(status_code=304)

    response = await call_next(request)

    # Кешируем ответ с изображением (только для аватаров)
    if response.status_code == 200 and request.url.path.startswith('/avatar/'):
        cache_key = f"img_{request.url.path}"
        etag = hashlib.md5(str(response.body).encode()).hexdigest()
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "public, max-age=86400"

        IMAGE_RESPONSE_CACHE[cache_key] = {
            'etag': etag,
            'body': response.body
        }

    return response


@app.get("/static/logo.jpg")
async def get_logo():
    """Отдача логотипа сайта"""
    BASE_DIR = Path(__file__).resolve().parent
    logo_path = BASE_DIR / "static" / "logo.jpg"

    if await run_in_threadpool(logo_path.exists):
        headers = {
            "Cache-Control": "public, max-age=86400",
            "Content-Type": "image/jpeg"
        }
        return FileResponse(logo_path, headers=headers)

    # Если нет JPG, пробуем PNG
    logo_png = BASE_DIR / "static" / "logo.png"
    if await run_in_threadpool(logo_png.exists):
        headers = {
            "Cache-Control": "public, max-age=86400",
            "Content-Type": "image/png"
        }
        return FileResponse(logo_png, headers=headers)

    raise HTTPException(status_code=404, detail="Логотип не найден")


# ОСТАВЛЯЕМ СТАРЫЙ МИДЛВАР ДЛЯ СОВМЕСТИМОСТИ
@app.middleware("http")
async def performance_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time

    if process_time > 1.0:
        print(f"⏱️ SLOW_REQUEST: {request.method} {request.url} - {process_time:.3f}s")

    response.headers["X-Process-Time"] = f"{process_time:.3f}s"
    return response


# --- ФЕДЕРАЛЬНЫЙ МОНИТОРИНГ (ИСПРАВЛЕННЫЙ) ---
@app.get("/{uid}/food/", response_class=HTMLResponse)
async def federal_index(
        request: Request,
        uid: int,
        year: Optional[str] = None,
        month: Optional[str] = None,
        db: Session = Depends(get_db)
):
    BASE_DIR = Path(__file__).resolve().parent
    base_path = BASE_DIR / str(uid) / "food"

    if not await run_in_threadpool(base_path.exists):
        return templates.TemplateResponse(
            "public_menu.html",
            {
                "request": request,
                "uid": uid,
                "school_name": f"Школа №{uid}",
                "total_files_count": 0,
                "years_count": 0,
                "years_label": "лет",
                "today_file": None,
                "today_date": datetime.now().strftime("%d.%m.%Y"),
                "today_weekday": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][datetime.now().weekday()],
                "years": [],
                "special_files": {"tm": [], "kp": [], "findex": []},
                "available_years": [str(datetime.now().year)],
                "filter_year": year or str(datetime.now().year),
                "filter_month": month or "",
                "months_items": list(MONTHS.items()),
            },
        )

    manifest_path = base_path / "manifest.json"
    manifest = await read_manifest_optimized(manifest_path)

    user = await get_cached_user(uid, db)
    school_name = user.unit_name if user else f"Школа №{uid}"

    ctx = await build_public_menu_context(uid, base_path, manifest, school_name, year, month)
    ctx["request"] = request
    return templates.TemplateResponse("public_menu.html", ctx)


# ИСПРАВЛЕННЫЙ эндпоинт для ФЦМПО
@app.get("/{uid}/food/{filename}")
async def get_federal_file(uid: int, filename: str):
    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / str(uid) / "food" / filename

    # Просто проверяем существование файла
    try:
        if await run_in_threadpool(file_path.exists):
            return FileResponse(
                file_path,
                filename=filename,
                headers={"Cache-Control": "public, max-age=3600"}
            )
    except Exception as e:
        print(f"Ошибка при доступе к файлу {file_path}: {e}")

    raise HTTPException(status_code=404, detail="Файл не найден")


# --- ОБНОВЛЕННЫЙ ЭНДПОИНТ ДЛЯ АВАТАРА С ОПТИМИЗАЦИЕЙ ---
@app.get("/{uid}/avatar/{filename:path}")
async def get_avatar(
        request: Request,
        uid: int,
        filename: str,
        size: str = "medium"  # small, medium, large
):
    """Отдача оптимизированного аватара школы"""
    BASE_DIR = Path(__file__).resolve().parent

    # Проверяем, запрашивается ли превью
    if filename.startswith('thumbnails/'):
        avatar_path = BASE_DIR / str(uid) / "avatar" / filename
    else:
        original_path = BASE_DIR / str(uid) / "avatar" / filename

        if not await run_in_threadpool(original_path.exists):
            raise HTTPException(status_code=404, detail="Аватар не найден")

        # Проверяем заголовки кеширования
        if_modified_since = request.headers.get('if-modified-since')
        if if_modified_since:
            try:
                mod_time = datetime.strptime(if_modified_since, '%a, %d %b %Y %H:%M:%S GMT')
                file_mod_time = datetime.fromtimestamp(
                    (await run_in_threadpool(original_path.stat)).st_mtime
                )
                if file_mod_time <= mod_time:
                    return Response(status_code=304)
            except:
                pass

        # Получаем оптимизированную версию
        try:
            avatar_path = await get_thumbnail_path(original_path, size)
        except Exception as e:
            logger.error(f"Ошибка получения оптимизированного аватара: {e}")
            avatar_path = original_path

    if await run_in_threadpool(avatar_path.exists):
        # Добавляем заголовки для кеширования
        headers = {
            "Cache-Control": "public, max-age=86400",  # Кеш на сутки
            "ETag": hashlib.md5(str(avatar_path.stat().st_mtime).encode()).hexdigest(),
            "Last-Modified": datetime.fromtimestamp(
                avatar_path.stat().st_mtime
            ).strftime('%a, %d %b %Y %H:%M:%S GMT')
        }

        return FileResponse(
            avatar_path,
            headers=headers
        )

    raise HTTPException(status_code=404, detail="Аватар не найден")


# --- РЕГИСТРАЦИЯ И АВТОРИЗАЦИЯ ---
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


@app.post("/register", response_class=HTMLResponse)
async def register(
        email: str = Form(...),
        password: str = Form(...),
        unit_name: str = Form(...),
        director_name: str = Form(...),
        district: str = Form(...),
        region: str = Form(...),  # ДОБАВИТЬ ЭТО ПОЛЕ
        food_type: str = Form(...),
        secret_code: Optional[str] = Form(None),
        db: Session = Depends(get_db)
):
    existing_user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )
    if existing_user:
        return "Пользователь с таким email уже существует"

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
        director_name=director_name,
        district=district,
        region=region,  # ДОБАВИТЬ ЭТО
        food_type=food_type,
        role=role
    )

    await run_in_threadpool(lambda: db.add(new_user))
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, new_user)

    BASE_DIR = Path(__file__).resolve().parent
    school_dir = BASE_DIR / str(new_user.id)
    food_dir = school_dir / "food"
    avatar_dir = school_dir / "avatar"  # Создаём папку для аватаров

    await run_in_threadpool(lambda: food_dir.mkdir(parents=True, exist_ok=True))
    await run_in_threadpool(lambda: avatar_dir.mkdir(parents=True, exist_ok=True))

    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
        request: Request,  # Добавлен request для сохранения в сессии
        email: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):
    user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )

    if not user or not auth.verify_password(password, user.hashed_password):
        return "Неверный логин или пароль"

    # Сохраняем в сессии для библиотеки знаний
    request.session["user_email"] = user.email
    request.session["user_id"] = user.id
    request.session["user_name"] = user.unit_name

    if "admin" in user.role:
        return RedirectResponse(f"/admin?admin_id={user.id}", status_code=303)
    return RedirectResponse(f"/dashboard?uid={user.id}", status_code=303)


# --- АДМИН-ПАНЕЛЬ ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(
        request: Request,
        admin_id: int,
        q: str = "",
        food_type: str = "",  # НОВЫЙ ПАРАМЕТР - фильтр по типу питания
        district: str = "",  # НОВЫЙ ПАРАМЕТР - фильтр по району (для регионального админа)
        page: int = 1,
        per_page: int = 60,
        db: Session = Depends(get_db)
):
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login")

    # Базовый запрос - только пользователи (не админы)
    query = db.query(models.User).filter(models.User.role == "user")

    # ========== ОГРАНИЧЕНИЕ ПО РЕГИОНУ/РАЙОНУ (безопасность) ==========
    # Региональный: region ИЛИ district из справочника районов региона
    #   (школы с пустым/другим region всё равно видны муниципальному админу)
    # Муниципальный: только свой район
    if admin.role == "municipal_admin":
        district = ""  # фильтр по району для муниципального не применяется

    query = apply_admin_school_scope(query, admin, db)

    # Общее число школ в зоне ответственности (до поисковых фильтров)
    unfiltered_total = await run_in_threadpool(query.count)

    # ========== ПОИСК ПО НАЗВАНИЮ ==========
    if q:
        query = query.filter(models.User.unit_name.ilike(f"%{q}%"))

    # ========== ФИЛЬТР ПО ТИПУ ПИТАНИЯ ==========
    if food_type:
        query = query.filter(models.User.food_type == food_type)

    # ========== ФИЛЬТР ПО РАЙОНУ (только для регионального админа) ==========
    if district and admin.role == "regional_admin":
        query = query.filter(func.trim(models.User.district) == district.strip())

    # ========== ПАГИНАЦИЯ ==========
    total_count = await run_in_threadpool(query.count)
    offset = (page - 1) * per_page
    schools = await run_in_threadpool(
        lambda: query.offset(offset).limit(per_page).all()
    )

    # ========== СПИСОК РАЙОНОВ ДЛЯ ФИЛЬТРА ==========
    districts_for_filter = []
    if admin.role == "regional_admin":
        districts_for_filter = await run_in_threadpool(
            lambda: get_districts_for_regional_admin(db, admin)
        )
    elif admin.role == "municipal_admin" and admin.district:
        districts_for_filter = [_norm_text(admin.district)]

    # ========== ОТВЕТ ==========
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "admin": admin,
        "schools": schools,
        "total_count": total_count,
        "unfiltered_total": unfiltered_total,
        "current_page": page,
        "per_page": per_page,
        "search_query": q,
        "food_type_filter": food_type,
        "district_filter": district,
        "food_types": FOOD_TYPES,
        "districts": districts_for_filter,
        "months": MONTHS,
    })


# --- МАССОВЫЕ ДЕЙСТВИЯ ---
@app.post("/bulk-upload")
async def bulk_upload(
        request: Request,
        admin_id: int = Form(...),
        year: str = Form(...),
        month: str = Form(...),
        school_ids: List[int] = Form(...),
        files: List[UploadFile] = File(...),
        db: Session = Depends(get_db)
):
    BASE_DIR = Path(__file__).resolve().parent
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login")

    # Базовый запрос к школам по переданным ID
    query = db.query(models.User).filter(
        models.User.id.in_(school_ids),
        models.User.role == "user"
    )

    if admin.role not in ("municipal_admin", "regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    query = apply_admin_school_scope(query, admin, db)

    schools = await run_in_threadpool(query.all)

    if not schools:
        return RedirectResponse(
            f"/admin?admin_id={admin_id}&message={urllib.parse.quote('Не выбрано ни одной доступной школы для рассылки.')}",
            status_code=303
        )

    uploader_name = admin.unit_name if admin else f"ADMIN {admin_id}"
    uploader_ip = request.client.host if request.client else "—"

    current_time = get_msk_time()

    temp_uploads = BASE_DIR / "temp_uploads"
    await asyncio.to_thread(lambda: temp_uploads.mkdir(parents=True, exist_ok=True))

    original_paths = {}
    for file in files:
        if not file.filename:
            continue
        orig_path = temp_uploads / file.filename
        await save_uploaded_file_optimized(file, orig_path)
        original_paths[file.filename] = orig_path

    for school in schools:
        food_path = BASE_DIR / str(school.id) / "food"
        await run_in_threadpool(lambda: food_path.mkdir(parents=True, exist_ok=True))
        manifest_path = food_path / "manifest.json"

        manifest = await read_manifest_optimized(manifest_path)

        for file in files:
            if not file.filename:
                continue

            orig_path = original_paths[file.filename]
            dest_path = food_path / file.filename

            await asyncio.to_thread(lambda: shutil.copy2(orig_path, dest_path))

            # ========== ИЗВЛЕКАЕМ ДАТУ ИЗ ИМЕНИ ФАЙЛА ==========
            date_from_filename = None
            filename = file.filename

            date_patterns = [
                r'(\d{2})[.-](\d{2})[.-](\d{4})',
                r'(\d{2})[./](\d{2})[./](\d{4})',
                r'(\d{4})[.-](\d{2})[.-](\d{2})',
            ]

            for pattern in date_patterns:
                match = re.search(pattern, filename)
                if match:
                    groups = match.groups()
                    if len(groups[0]) == 4:
                        y, m, d = groups[0], groups[1], groups[2]
                    else:
                        d, m, y = groups[0], groups[1], groups[2]
                    date_from_filename = f"{d}.{m}.{y}"
                    break

            if not date_from_filename:
                date_from_filename = f"{year}-{month}-01"
                date_from_filename = f"01.{month}.{year}"

            manifest[file.filename] = {
                "assigned_year": year,
                "assigned_month": month,
                "uploader_name": uploader_name,
                "uploader_ip": uploader_ip,
                "upload_datetime": current_time.strftime("%d.%m.%Y %H:%M"),
                "embedded_date": date_from_filename
            }

            await update_excel_content(
                dest_path,
                school.unit_name,
                school.director_name,
                year,
                date_from_filename
            )

        await write_manifest_optimized(manifest_path, manifest)

    try:
        await asyncio.to_thread(lambda: shutil.rmtree(temp_uploads))
    except:
        pass

    msg = f"Рассылка выполнена для {len(schools)} школ."
    return RedirectResponse(
        f"/admin?admin_id={admin_id}&message={urllib.parse.quote(msg)}",
        status_code=303
    )


@app.post("/admin/bulk-delete-files")
async def bulk_delete_files(
        request: Request,
        admin_id: int = Form(...),
        school_ids: List[int] = Form(...),
        delete_all: bool = Form(False),
        keep_exceptions: bool = Form(False),
        only_kp: bool = Form(False),
        only_tm_sm: bool = Form(False),
        only_findex: bool = Form(False),
        db: Session = Depends(get_db)
):
    BASE_DIR = Path(__file__).resolve().parent
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    # Получаем школы по ID с учетом прав админа
    schools_query = db.query(models.User).filter(
        models.User.id.in_(school_ids),
        models.User.role == "user"
    )
    if admin.role not in ("municipal_admin", "regional_admin"):
        return RedirectResponse("/login", status_code=303)
    schools_query = apply_admin_school_scope(schools_query, admin, db)

    schools = await run_in_threadpool(schools_query.all)

    deleted_count = 0
    errors = []
    deleted_files_list = []

    for school in schools:
        food_path = BASE_DIR / str(school.id) / "food"
        manifest_path = food_path / "manifest.json"

        if not await run_in_threadpool(food_path.exists):
            continue

        # Получаем все файлы в директории
        try:
            all_files = await list_directory_files_optimized(food_path)
        except Exception as e:
            errors.append(f"Ошибка при чтении папки школы {school.unit_name}: {str(e)}")
            continue

        # Загружаем манифест для метаданных
        manifest = await read_manifest_optimized(manifest_path)

        files_to_delete = []

        for file_path in all_files:
            filename = file_path.name

            # Пропускаем manifest.json
            if filename == "manifest.json":
                continue

            should_delete = False

            # Определяем, нужно ли удалять файл
            if delete_all:
                # Удаляем все файлы
                if keep_exceptions:
                    # Кроме исключений
                    if filename == "findex.xlsx":
                        continue
                    if re.match(r"^tm\d{4}-sm\.xlsx$", filename):
                        continue
                    if re.match(r"^kp\d{4}\.xlsx$", filename):
                        continue
                should_delete = True

            elif only_tm_sm:
                # Только tm-файлы
                if re.match(r"^tm\d{4}-sm\.xlsx$", filename):
                    should_delete = True

            elif only_findex:
                # Только findex.xlsx
                if filename == "findex.xlsx":
                    should_delete = True

            elif only_kp:
                # Только kp-файлы
                if re.match(r"^kp\d{4}\.xlsx$", filename):
                    should_delete = True

            elif keep_exceptions and not any([delete_all, only_tm_sm, only_findex, only_kp]):
                # Удаляем всё кроме исключений
                if filename not in ["findex.xlsx"] and \
                        not re.match(r"^tm\d{4}-sm\.xlsx$", filename) and \
                        not re.match(r"^kp\d{4}\.xlsx$", filename):
                    should_delete = True

            if should_delete:
                files_to_delete.append(file_path)

        # Удаляем файлы
        for file_path in files_to_delete:
            try:
                # Удаляем физический файл
                await delete_file_optimized(file_path)

                # Удаляем запись из манифеста
                if file_path.name in manifest:
                    manifest.pop(file_path.name)

                deleted_count += 1
                deleted_files_list.append(f"{school.unit_name}: {file_path.name}")

            except Exception as e:
                errors.append(f"Ошибка при удалении {file_path.name} у {school.unit_name}: {str(e)}")

        # Сохраняем обновленный манифест
        if files_to_delete:
            await write_manifest_optimized(manifest_path, manifest)

    # Формируем сообщение о результате
    if deleted_count > 0:
        msg = f"✅ Успешно удалено {deleted_count} файлов"
        if deleted_files_list:
            # Показываем первые 5 удаленных файлов
            sample = deleted_files_list[:5]
            msg += f": {', '.join(sample)}"
            if len(deleted_files_list) > 5:
                msg += f" и ещё {len(deleted_files_list) - 5}"
    else:
        msg = "ℹ️ Файлы для удаления не найдены"

    if errors:
        msg += f". ⚠️ Ошибки: {'; '.join(errors[:3])}"
        if len(errors) > 3:
            msg += f" и ещё {len(errors) - 3} ошибок"

    return RedirectResponse(
        f"/admin?admin_id={admin_id}&message={msg}",
        status_code=303
    )


# НОВЫЙ ЭНДПОИНТ: Массовое удаление файлов по месяцам
@app.post("/admin/bulk-delete-files-by-month")
async def bulk_delete_files_by_month(
        request: Request,
        admin_id: int = Form(...),
        school_ids: List[int] = Form(...),
        months: List[str] = Form(...),
        year: str = Form(...),
        delete_all_months: bool = Form(False),
        db: Session = Depends(get_db)
):
    BASE_DIR = Path(__file__).resolve().parent
    admin = await get_cached_user(admin_id, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    # Получаем школы по ID с учетом прав админа
    schools_query = db.query(models.User).filter(
        models.User.id.in_(school_ids),
        models.User.role == "user"
    )
    if admin.role not in ("municipal_admin", "regional_admin"):
        return RedirectResponse("/login", status_code=303)
    schools_query = apply_admin_school_scope(schools_query, admin, db)

    schools = await run_in_threadpool(schools_query.all)

    deleted_count = 0
    errors = []
    deleted_files_list = []

    # Если выбран "Все месяцы", очищаем список и будем удалять все месяцы
    selected_months = None if delete_all_months else months

    for school in schools:
        food_path = BASE_DIR / str(school.id) / "food"
        manifest_path = food_path / "manifest.json"

        if not await run_in_threadpool(food_path.exists):
            continue

        # Получаем все файлы в директории
        try:
            all_files = await list_directory_files_optimized(food_path)
        except Exception as e:
            errors.append(f"Ошибка при чтении папки школы {school.unit_name}: {str(e)}")
            continue

        # Загружаем манифест для метаданных
        manifest = await read_manifest_optimized(manifest_path)

        files_to_delete = []

        for file_path in all_files:
            filename = file_path.name

            # Пропускаем manifest.json и специальные файлы
            if filename == "manifest.json" or \
                    filename == "findex.xlsx" or \
                    re.match(r"^tm\d{4}-sm\.xlsx$", filename) or \
                    re.match(r"^kp\d{4}\.xlsx$", filename):
                continue

            # Получаем метаданные файла из манифеста
            file_meta = manifest.get(filename, {})
            file_year = file_meta.get("assigned_year")
            file_month = file_meta.get("assigned_month")

            # Если в манифесте нет данных, пробуем извлечь из имени файла
            if not file_year or not file_month:
                date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
                if date_match:
                    file_year, file_month, _ = date_match.groups()

            # Проверяем, соответствует ли файл критериям удаления
            if file_year == year:
                if delete_all_months or (file_month and file_month in selected_months):
                    files_to_delete.append(file_path)

        # Удаляем файлы
        for file_path in files_to_delete:
            try:
                # Удаляем физический файл
                await delete_file_optimized(file_path)

                # Удаляем запись из манифеста
                if file_path.name in manifest:
                    manifest.pop(file_path.name)

                deleted_count += 1
                deleted_files_list.append(f"{school.unit_name}: {file_path.name}")

            except Exception as e:
                errors.append(f"Ошибка при удалении {file_path.name} у {school.unit_name}: {str(e)}")

        # Сохраняем обновленный манифест
        if files_to_delete:
            await write_manifest_optimized(manifest_path, manifest)

    # Формируем сообщение о результате
    if deleted_count > 0:
        if delete_all_months:
            period = f"за ВСЕ месяцы {year} года"
        else:
            month_names = [MONTHS.get(m, m) for m in selected_months]
            period = f"за {', '.join(month_names)} {year} года"

        msg = f"✅ Успешно удалено {deleted_count} файлов {period}"
        if deleted_files_list:
            # Показываем первые 5 удаленных файлов
            sample = deleted_files_list[:5]
            msg += f": {', '.join(sample)}"
            if len(deleted_files_list) > 5:
                msg += f" и ещё {len(deleted_files_list) - 5}"
    else:
        msg = "ℹ️ Файлы для удаления не найдены"

    if errors:
        msg += f". ⚠️ Ошибки: {'; '.join(errors[:3])}"
        if len(errors) > 3:
            msg += f" и ещё {len(errors) - 3} ошибок"

    return RedirectResponse(
        f"/admin?admin_id={admin_id}&message={msg}",
        status_code=303
    )


# --- ЛИЧНЫЙ КАБИНЕТ ШКОЛЫ (ОБНОВЛЁННЫЙ) ---
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
        request: Request,
        uid: int,
        year: str = None,
        month: str = None,
        view: str = "list",
        db: Session = Depends(get_db)
):
    user = await get_cached_user(uid, db)
    if not user:
        return RedirectResponse("/login")

    BASE_DIR = Path(__file__).resolve().parent
    food_path = BASE_DIR / str(uid) / "food"
    profile_path = BASE_DIR / str(uid) / "profile.json"

    # Проверка существования папки
    if not await run_in_threadpool(food_path.exists):
        await run_in_threadpool(lambda: food_path.mkdir(parents=True, exist_ok=True))
        manifest_path = food_path / "manifest.json"
        await write_manifest_optimized(manifest_path, {})
        print(f"📁 Создана папка для пользователя {uid} при входе в дашборд")

    # Загружаем данные профиля
    profile_data = {}
    if await run_in_threadpool(profile_path.exists):
        try:
            async with aiofiles.open(profile_path, "r", encoding="utf-8") as f:
                content = await f.read()
                profile_data = json.loads(content) if content else {}
        except Exception as e:
            logger.error(f"Ошибка загрузки profile.json для uid {uid}: {e}")

    manifest_path = food_path / "manifest.json"
    manifest = await read_manifest_optimized(manifest_path)

    async with CACHE_LOCK:
        MANIFEST_CACHE[str(manifest_path)] = manifest.copy()

    files = await list_directory_files_optimized(food_path)

    # ---------- Группировка обычных файлов по годам и месяцам ----------
    grouped_files = {}
    # ---------- НОВОЕ: список для специальных файлов (tm, kp, findex) ----------
    special_files_all = []

    for f in files:
        if f.name == "manifest.json":
            continue

        file_meta = manifest.get(f.name, {})

        # Получаем год и месяц из метаданных
        assigned_year = file_meta.get("assigned_year")
        assigned_month = file_meta.get("assigned_month")

        # День меню из имени (YYYY-MM-DD-sm.xlsx)
        day = None
        menu_date = None
        full_date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', f.name)
        if full_date_match:
            # Для ежедневных меню приоритет — дата в имени файла
            assigned_year = full_date_match.group(1)
            assigned_month = full_date_match.group(2)
            day = full_date_match.group(3)
            menu_date = f"{day}.{assigned_month}.{assigned_year}"
        elif not assigned_year or not assigned_month:
            date_match = re.search(r'(\d{4})-(\d{2})', f.name)
            if date_match:
                assigned_year = date_match.group(1)
                assigned_month = date_match.group(2)
            else:
                assigned_year = str(get_msk_time().year)
                assigned_month = get_msk_time().strftime("%m")

        month_name = MONTHS.get(assigned_month, assigned_month)

        if "upload_datetime" in file_meta:
            upload_time = file_meta["upload_datetime"]
        else:
            mtime = f.stat().st_mtime
            dt = datetime.utcfromtimestamp(mtime) + timedelta(hours=3)
            upload_time = dt.strftime("%d.%m.%Y %H:%M")

        uploader_name = file_meta.get("uploader_name", "—")
        uploader_ip = file_meta.get("uploader_ip", "—")

        # ---------- ОПРЕДЕЛЯЕМ, ЯВЛЯЕТСЯ ЛИ ФАЙЛ СПЕЦИАЛЬНЫМ ----------
        is_special = False
        if re.match(r'^tm\d{4}-sm\.xlsx$', f.name):
            is_special = True
        elif re.match(r'^kp\d{4}\.xlsx$', f.name):
            is_special = True
        elif f.name == "findex.xlsx":
            is_special = True

        if is_special:
            special_files_all.append({
                "filename": f.name,
                "year": assigned_year,
                "month": assigned_month,
                "month_name": month_name,
                "date": upload_time,
                "uploader": uploader_name,
                "ip": uploader_ip,
                "size": f.stat().st_size,
            })
        else:
            grouped_files.setdefault(assigned_year, {}).setdefault(month_name, []).append({
                "filename": f.name,
                "date": upload_time,
                "uploader": uploader_name,
                "ip": uploader_ip,
                "day": day,
                "menu_date": menu_date,
            })

    special_files_all.sort(
        key=lambda x: (x["year"], x["month"]),
        reverse=True
    )

    if not year or not month:
        if grouped_files:
            latest_year = max(grouped_files.keys())
            if grouped_files[latest_year]:
                latest_month = max(grouped_files[latest_year].keys())
                for num, name in MONTHS.items():
                    if name == latest_month:
                        month = num
                        break
                year = latest_year
            else:
                year = "2025"
                month = "05"
        else:
            year = "2025"
            month = "05"

    # Данные для календарного вида текущего периода
    month_name_current = MONTHS.get(month, month)
    period_files = list(grouped_files.get(year, {}).get(month_name_current, []))
    files_by_day = {}
    undated_files = []
    for fi in period_files:
        if fi.get("day"):
            files_by_day.setdefault(fi["day"], []).append(fi)
        else:
            undated_files.append(fi)

    try:
        y_int, m_int = int(year), int(month)
        pycal.setfirstweekday(pycal.MONDAY)
        calendar_weeks = pycal.monthcalendar(y_int, m_int)
    except (TypeError, ValueError):
        calendar_weeks = []

    view_mode = "calendar" if (view or "").lower() in ("calendar", "cal", "календарь") else "list"

    monitoring_url = f"{request.base_url}{uid}/food/"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "profile": profile_data,
        "files_grouped": grouped_files,
        "special_files_all": special_files_all,
        "period": f"{year}-{month}",
        "year": year,
        "month": month,
        "months": MONTHS,
        "monitoring_url": monitoring_url,
        "food_types": FOOD_TYPES,
        "files_by_day": files_by_day,
        "undated_files": undated_files,
        "calendar_weeks": calendar_weeks,
        "weekday_labels": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        "view_mode": view_mode,
        "period_files_count": len(period_files),
        "special_files_count": len(special_files_all),
        "fcmp_stats": await run_in_threadpool(fcmp_service.get_school_fcmp_stats, db, uid),
    })


def _safe_school_food_file(uid: int, name: str) -> Path:
    """Файл из папки школы. Имя без путей и без служебного manifest."""
    if not name or name != Path(name).name or name.startswith("."):
        raise HTTPException(status_code=400, detail="Некорректное имя файла")
    if name == "manifest.json":
        raise HTTPException(status_code=404, detail="Файл не найден")
    base = (Path(__file__).resolve().parent / str(uid) / "food").resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return path


def _preview_cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M") if value.hour or value.minute else value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value).replace("\n", " ").strip()


def _trim_preview_rows(rows):
    rows = [list(row) for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    last_col = 0
    for row in padded:
        for index, cell in enumerate(row):
            if cell.strip():
                last_col = max(last_col, index)
    return [row[: last_col + 1] for row in padded]


def _header_row_index(rows):
    hints = ("блюдо", "наименование", "прием пищи", "раздел", "день недели")
    for index, row in enumerate(rows[:8]):
        joined = " ".join(row).lower()
        if any(hint in joined for hint in hints):
            return index
    return 0 if rows else None


def _preview_workbook(path: Path):
    max_sheets = 4
    max_rows = 80
    max_cols = 14
    wb = load_workbook(path, data_only=True, read_only=True)
    sheets = []
    try:
        for ws in wb.worksheets[:max_sheets]:
            raw_rows = []
            truncated = False
            for index, row in enumerate(ws.iter_rows(max_row=max_rows + 1, max_col=max_cols, values_only=True)):
                if index >= max_rows:
                    truncated = True
                    break
                raw_rows.append([_preview_cell(value) for value in row])
            rows = _trim_preview_rows(raw_rows)
            sheets.append({
                "name": ws.title,
                "rows": rows,
                "header": _header_row_index(rows),
                "truncated": truncated,
            })
    finally:
        wb.close()
    return sheets


def _preview_csv(path: Path):
    import csv
    raw = path.read_bytes()[:1_500_000]
    text = None
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    raw_rows = []
    truncated = False
    for index, row in enumerate(reader):
        if index >= 80:
            truncated = True
            break
        raw_rows.append([_preview_cell(cell) for cell in row[:14]])
    rows = _trim_preview_rows(raw_rows)
    return [{
        "name": "Таблица",
        "rows": rows,
        "header": _header_row_index(rows),
        "truncated": truncated,
    }]


def build_school_file_preview(uid: int, name: str):
    path = _safe_school_food_file(uid, name)
    ext = path.suffix.lower()
    quoted = urllib.parse.quote(path.name)
    download_url = f"/{uid}/food/{quoted}"
    inline_url = f"/api/school/{uid}/file-inline?name={quoted}"
    if path.stat().st_size > 12 * 1024 * 1024 and ext in {".xlsx", ".xlsm", ".csv"}:
        return {
            "kind": "download",
            "filename": path.name,
            "url": download_url,
            "message": "Файл слишком большой для предпросмотра",
        }
    if ext in {".xlsx", ".xlsm"}:
        try:
            sheets = _preview_workbook(path)
        except (InvalidFileException, OSError, ValueError) as exc:
            logger.warning("Preview failed for %s: %s", path, exc)
            return {
                "kind": "download",
                "filename": path.name,
                "url": download_url,
                "message": "Не удалось прочитать таблицу",
            }
        return {
            "kind": "sheet",
            "filename": path.name,
            "url": download_url,
            "sheets": sheets,
        }
    if ext == ".csv":
        return {
            "kind": "sheet",
            "filename": path.name,
            "url": download_url,
            "sheets": _preview_csv(path),
        }
    if ext == ".pdf":
        return {"kind": "pdf", "filename": path.name, "url": download_url, "inline": inline_url}
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return {"kind": "image", "filename": path.name, "url": download_url, "inline": inline_url}
    return {
        "kind": "download",
        "filename": path.name,
        "url": download_url,
        "message": "Для этого формата доступно только скачивание",
    }


@app.get("/api/school/{uid}/file-preview")
async def school_file_preview(uid: int, name: str):
    return await run_in_threadpool(build_school_file_preview, uid, name)


@app.get("/api/school/{uid}/file-inline")
async def school_file_inline(uid: int, name: str):
    path = await run_in_threadpool(_safe_school_food_file, uid, name)
    media = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media,
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=300"},
    )


# --- СТАТИСТИКА ФЦМПО (Чеченская Республика) ---
@app.get("/api/school/{uid}/fcmp-stats")
async def get_fcmp_stats(uid: int, db: Session = Depends(get_db)):
    user = await get_cached_user(uid, db)
    if not user:
        raise HTTPException(status_code=404, detail="Школа не найдена")
    return await run_in_threadpool(fcmp_service.get_school_fcmp_stats, db, uid)


@app.post("/api/school/{uid}/fcmp-stats/refresh")
async def refresh_fcmp_stats(uid: int, db: Session = Depends(get_db)):
    user = await get_cached_user(uid, db)
    if not user:
        raise HTTPException(status_code=404, detail="Школа не найдена")
    try:
        return await run_in_threadpool(fcmp_service.sync_school_fcmp_stats, db, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("FCMP refresh failed for uid=%s", uid)
        raise HTTPException(status_code=502, detail=f"Не удалось получить данные ФЦМПО: {e}")


@app.get("/api/school/{uid}/fcmp-bind-link/preview")
async def preview_fcmp_bind_link(
    uid: int,
    link: str,
    db: Session = Depends(get_db),
):
    """Предпросмотр: какой пищеблок и какая ссылка уйдут в ФЦМПО."""
    user = await get_cached_user(uid, db)
    if not user:
        raise HTTPException(status_code=404, detail="Школа не найдена")
    try:
        return await run_in_threadpool(fcmp_service.preview_bind_external_link, db, user, link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("FCMP bind preview failed for uid=%s", uid)
        raise HTTPException(status_code=502, detail=f"Не удалось проверить пищеблок ФЦМПО: {e}")


@app.post("/api/school/{uid}/fcmp-bind-link")
async def bind_fcmp_link(
    uid: int,
    db: Session = Depends(get_db),
    link: str = Form(...),
    pin: str = Form(""),
):
    """Привязать внешний путь школы как ссылку пищеблока в базе ФЦМПО."""
    user = await get_cached_user(uid, db)
    if not user:
        raise HTTPException(status_code=404, detail="Школа не найдена")
    try:
        return await run_in_threadpool(fcmp_service.bind_external_link, db, user, link, pin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("FCMP bind link failed for uid=%s", uid)
        raise HTTPException(status_code=502, detail=f"Не удалось изменить ссылку в ФЦМПО: {e}")


@app.get("/api/admin/{admin_id}/fcmp-dashboard")
async def admin_fcmp_dashboard(
    admin_id: int,
    date: str = "",
    rayon: str = "",
    db: Session = Depends(get_db),
):
    """Сводная статистика ФЦМПО для регионального/муниципального админа."""
    from datetime import date as date_cls

    admin = await get_cached_user(admin_id, db)
    if not admin or admin.role not in ("regional_admin", "municipal_admin"):
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")

    stat_date = None
    if date.strip():
        try:
            stat_date = date_cls.fromisoformat(date.strip()[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректная дата (ожидается YYYY-MM-DD)")

    # Муниципальный админ не может запрашивать чужой район
    rayon_arg = rayon.strip() if admin.role == "regional_admin" else ""

    try:
        return await run_in_threadpool(
            fcmp_service.get_admin_fcmp_dashboard,
            admin,
            stat_date,
            rayon_arg or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("FCMP admin dashboard failed for admin_id=%s", admin_id)
        raise HTTPException(status_code=502, detail=f"Не удалось получить статистику ФЦМПО: {e}")


# --- ОБНОВЛЕННАЯ ЗАГРУЗКА АВАТАРА С ОПТИМИЗАЦИЕЙ ---
@app.post("/profile/upload-avatar")
async def upload_avatar(
        uid: int = Form(...),
        avatar: UploadFile = File(...)
):
    BASE_DIR = Path(__file__).resolve().parent
    school_dir = BASE_DIR / str(uid)
    avatar_dir = school_dir / "avatar"
    profile_path = school_dir / "profile.json"

    # Создаём папки
    await run_in_threadpool(lambda: avatar_dir.mkdir(parents=True, exist_ok=True))

    # Удаляем старый аватар и его превью
    try:
        old_files = await run_in_threadpool(lambda: list(avatar_dir.glob("*")))
        old_files.extend(await run_in_threadpool(lambda: list(avatar_dir.glob("thumbnails/*"))))
        for old_file in old_files:
            await run_in_threadpool(old_file.unlink)
    except Exception as e:
        logger.error(f"Ошибка при удалении старого аватара: {e}")

    # Проверяем тип файла
    file_ext = Path(avatar.filename).suffix.lower()
    allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']

    if file_ext not in allowed_extensions:
        file_ext = '.jpg'  # По умолчанию

    # Сохраняем временный файл
    temp_path = avatar_dir / f"temp_{int(time.time())}{file_ext}"
    content = await avatar.read()

    async with aiofiles.open(temp_path, "wb") as f:
        await f.write(content)

    try:
        # Оптимизируем основное изображение
        avatar_filename = f"avatar{file_ext}"
        avatar_path = avatar_dir / avatar_filename

        result = await optimize_image_async(
            temp_path,
            output_path=avatar_path,
            max_size=MAX_IMAGE_SIZE,
            quality=JPEG_QUALITY
        )

        if result and result['saved_percent'] > 0:
            logger.info(f"Аватар оптимизирован: сэкономлено {result['saved_percent']:.1f}%")

        # Создаем превью разных размеров
        for size_name in THUMBNAIL_SIZES.keys():
            await get_thumbnail_path(avatar_path, size_name)

        # Удаляем временный файл
        await delete_file_optimized(temp_path)

    except Exception as e:
        logger.error(f"Ошибка при оптимизации аватара: {e}")
        # Если оптимизация не удалась, используем оригинал
        if temp_path.exists():
            avatar_path = avatar_dir / f"avatar{file_ext}"
            await asyncio.to_thread(shutil.move, str(temp_path), str(avatar_path))

    # Обновляем profile.json
    profile_data = {}
    if await run_in_threadpool(profile_path.exists):
        try:
            async with aiofiles.open(profile_path, "r", encoding="utf-8") as f:
                content = await f.read()
                profile_data = json.loads(content) if content else {}
        except Exception:
            profile_data = {}

    profile_data["avatar"] = f"avatar{file_ext}"

    async with aiofiles.open(profile_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(profile_data, ensure_ascii=False, indent=2))

    # Очищаем кеш для этого аватара
    cache_key = f"img_/{uid}/avatar/avatar{file_ext}"
    if cache_key in IMAGE_RESPONSE_CACHE:
        del IMAGE_RESPONSE_CACHE[cache_key]

    return RedirectResponse(f"/dashboard?uid={uid}", status_code=303)


# --- ОБНОВЛЕНИЕ ССЫЛОК ---
@app.post("/profile/update-links")
async def update_links(
        uid: int = Form(...),
        website_url: str = Form(""),
        hot_meal_url: str = Form("")
):
    BASE_DIR = Path(__file__).resolve().parent
    school_dir = BASE_DIR / str(uid)
    profile_path = school_dir / "profile.json"

    profile_data = {}
    if await run_in_threadpool(profile_path.exists):
        try:
            async with aiofiles.open(profile_path, "r", encoding="utf-8") as f:
                content = await f.read()
                profile_data = json.loads(content) if content else {}
        except Exception:
            profile_data = {}

    if website_url:
        profile_data["website_url"] = website_url
    if hot_meal_url:
        profile_data["hot_meal_url"] = hot_meal_url

    async with aiofiles.open(profile_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(profile_data, ensure_ascii=False, indent=2))

    return RedirectResponse(f"/dashboard?uid={uid}", status_code=303)


# --- ОСТАЛЬНЫЕ ЭНДПОИНТЫ ---
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

    # Получаем текущую дату для загрузки
    current_time = get_msk_time()

    for file in files:
        if not file.filename:
            continue

        # Проверяем, не существует ли уже файл с таким именем
        dest_path = food_path / file.filename

        # ========== ИСПРАВЛЕНИЕ: Сохраняем метаданные с правильными годом и месяцем ==========
        manifest[file.filename] = {
            "assigned_year": year,  # Год из формы
            "assigned_month": month,  # Месяц из формы
            "uploader_name": uploader_name,
            "uploader_ip": client_ip,
            "upload_datetime": current_time.strftime("%d.%m.%Y %H:%M")
        }

        # Сохраняем файл
        await save_uploaded_file_optimized(file, dest_path)

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

    # Перенаправляем обратно с сохранением года и месяца
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

    for filename in files:
        file_path = folder / filename
        await delete_file_optimized(file_path)
        manifest.pop(filename, None)

    await write_manifest_optimized(manifest_path, manifest)

    return RedirectResponse(
        f"/dashboard?uid={uid}&year={year}&month={month}",
        status_code=303
    )


@app.post("/profile/update")
async def update_profile(
        uid: int = Form(...),
        director_name: str = Form(""),
        unit_name: str | None = Form(None),
        db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if director_name != "":
        user.director_name = director_name

    if unit_name is not None and unit_name.strip() != "":
        user.unit_name = unit_name.strip()

    db.commit()
    db.refresh(user)

    return RedirectResponse(f"/dashboard?uid={uid}", status_code=303)


# --- СБРОС ПАРОЛЯ ---
def is_valid_email(email: str) -> bool:
    if not email or '@' not in email:
        return False
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def get_smtp_config(email: str) -> dict:
    domain = email.lower().split('@')[-1]

    providers = {
        'gmail.com': {
            'hostname': 'smtp.gmail.com',
            'port': 587,
            'use_tls': False,
            'start_tls': True
        },
        'yandex.ru': {
            'hostname': 'smtp.yandex.ru',
            'port': 465,
            'use_tls': True,
            'start_tls': False
        },
        'mail.ru': {
            'hostname': 'smtp.mail.ru',
            'port': 465,
            'use_tls': True,
            'start_tls': False
        },
        'yahoo.com': {
            'hostname': 'smtp.mail.yahoo.com',
            'port': 587,
            'use_tls': False,
            'start_tls': True
        }
    }

    if domain in providers:
        return providers[domain]

    return {
        'hostname': f'smtp.{domain}',
        'port': 587,
        'use_tls': False,
        'start_tls': True
    }


async def send_reset_email(email: str, token: str):
    try:
        if not is_valid_email(email):
            raise ValueError("Некорректный email")

        reset_url = f"https://monitoring95.ru/reset-password/{token}"
        safe_email = email.replace('<', '&lt;').replace('>', '&gt;')

        message = MIMEText(f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Сброс пароля</title>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #4f46e5; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 30px; background: #f9fafb; }}
                .button {{ background: #4f46e5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; }}
                .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔐 Сброс пароля</h1>
                </div>
                <div class="content">
                    <p>Здравствуйте!</p>
                    <p>Мы получили запрос на сброс пароля для вашей учетной записи.</p>
                    <p>Для создания нового пароля нажмите на кнопку ниже:</p>
                    <p style="text-align: center;">
                        <a href="{reset_url}" class="button">Сменить пароль</a>
                    </p>
                    <p>Ссылка действительна в течение 1 часа.</p>
                    <p>Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо.</p>
                    <hr>
                    <p style="font-size: 12px; color: #6b7280;">Email: {safe_email}</p>
                </div>
                <div class="footer">
                    <p>© 2026 ЕЦМП Мониторинг питания</p>
                </div>
            </div>
        </body>
        </html>
        """, "html", "utf-8")

        message["Subject"] = "Сброс пароля"
        message["From"] = os.getenv("SMTP_USERNAME")
        message["To"] = email

        # Настройки SMTP для любых почтовых сервисов
        smtp_settings = {
            'hostname': 'smtp.yandex.ru',  # Используем ваш SMTP сервер
            'port': 465,
            'use_tls': True,
            'start_tls': False
        }

        smtp = aiosmtplib.SMTP(
            hostname=smtp_settings['hostname'],
            port=smtp_settings['port'],
            username=os.getenv("SMTP_USERNAME"),
            password=os.getenv("SMTP_PASSWORD"),
            use_tls=smtp_settings['use_tls'],
            start_tls=smtp_settings['start_tls']
        )

        await smtp.connect()
        await smtp.send_message(message)
        await smtp.quit()

    except Exception as e:
        print(f"Ошибка отправки письма на {email}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось отправить письмо: {str(e)}"
        )
@app.post("/profile/update-food-type")
async def update_food_type(
    uid: int = Form(...),
    food_type: str = Form(...),
    db: Session = Depends(get_db)
):
    """Обновление типа питания школы"""
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Проверяем, что переданный тип есть в списке допустимых
    if food_type not in FOOD_TYPES:
        raise HTTPException(status_code=400, detail="Недопустимый тип питания")

    user.food_type = food_type
    db.commit()
    db.refresh(user)

    # Инвалидируем кеш пользователя, чтобы изменения сразу подхватились
    cache_key = f"user_{uid}"
    async with CACHE_LOCK:
        if cache_key in USER_CACHE:
            del USER_CACHE[cache_key]

    return RedirectResponse(f"/dashboard?uid={uid}", status_code=303)

@app.get("/reset-password-request", response_class=HTMLResponse)
async def reset_password_request_page(request: Request):
    return templates.TemplateResponse("reset_password_request.html", {"request": request})


@app.post("/reset-password-request")
async def reset_password_request(
        request: Request,
        email: str = Form(...),
        db: Session = Depends(get_db)
):
    if not is_valid_email(email):
        return templates.TemplateResponse(
            "reset_password_request.html",
            {"request": request, "error": "Некорректный email"}
        )

    user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )

    if not user:
        return templates.TemplateResponse(
            "reset_password_request.html",
            {"request": request, "error": "Пользователь с таким email не найден"}
        )

    SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
    token = jwt.encode(
        {"sub": email, "exp": datetime.utcnow() + timedelta(hours=1)},
        SECRET_KEY,
        algorithm="HS256"
    )

    try:
        await send_reset_email(email, token)
    except HTTPException as e:
        return templates.TemplateResponse(
            "reset_password_request.html",
            {"request": request, "error": e.detail}
        )

    return templates.TemplateResponse(
        "reset_password_request.html",
        {"request": request, "success": "Письмо для сброса пароля отправлено!"}
    )


@app.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(
        request: Request,
        token: str
):
    SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        if not email:
            return HTMLResponse("<h2>Недействительная ссылка</h2>")
    except JWTError:
        return HTMLResponse("<h2>Недействительный или просроченный токен</h2>")

    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "email": email}
    )


@app.post("/reset-password")
async def reset_password(
        token: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db)
):
    SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=400, detail="Недействительный токен")
    except JWTError:
        raise HTTPException(status_code=400, detail="Просроченный токен")

    user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if len(password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Пароль должен быть не менее 6 символов"
        )

    hashed_pw = auth.get_password_hash(password)
    user.hashed_password = hashed_pw

    try:
        await run_in_threadpool(db.commit)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Не удалось сохранить новый пароль. Попробуйте снова."
        )

    return RedirectResponse("/login", status_code=303)

@app.get("/reset-password-direct", response_class=HTMLResponse)
async def reset_password_direct_page(request: Request):
    """Страница для сброса пароля с секретным кодом"""
    return templates.TemplateResponse("reset_password_direct.html", {"request": request})


@app.post("/reset-password-direct", response_class=HTMLResponse)
async def reset_password_direct(
        request: Request,
        email: str = Form(...),
        secret_code: str = Form(...),
        db: Session = Depends(get_db)
):
    DIRECT_RESET_CODE = os.getenv("DIRECT_RESET_CODE", "Y7x3#pLq8$zN5&kV9@m!rT4")

    if secret_code != DIRECT_RESET_CODE:
        return templates.TemplateResponse(
            "reset_password_direct.html",
            {"request": request, "error": "Неверный секретный код"}
        )

    user = await run_in_threadpool(
        lambda: db.query(models.User).filter(models.User.email == email).first()
    )
    if not user:
        return templates.TemplateResponse(
            "reset_password_direct.html",
            {"request": request, "error": "Пользователь с таким email не найден"}
        )

    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    new_password = ''.join(secrets.choice(alphabet) for _ in range(10))

    hashed_pw = auth.get_password_hash(new_password)
    user.hashed_password = hashed_pw
    await run_in_threadpool(db.commit)

    return templates.TemplateResponse(
        "reset_password_direct.html",
        {
            "request": request,
            "success": "Пароль успешно изменён!",
            "new_password": new_password
        }
    )

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": get_msk_time().isoformat(),
        "cache_stats": {
            "manifest_cache": len(MANIFEST_CACHE),
            "user_cache": len(USER_CACHE),
            "file_exists_cache": len(FILE_EXISTS_CACHE),
            "image_cache": len(IMAGE_RESPONSE_CACHE)
        }
    }


# --- НОВЫЙ ЭНДПОИНТ ДЛЯ СТАТИСТИКИ ПРОИЗВОДИТЕЛЬНОСТИ ---
@app.get("/performance-stats")
async def get_performance_stats(request: Request):
    """Статистика производительности (только для админов)"""

    if not request.session.get("dashboard_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    return {
        "cache_stats": {
            "manifest_cache": len(MANIFEST_CACHE),
            "user_cache": len(USER_CACHE),
            "file_exists_cache": len(FILE_EXISTS_CACHE),
            "image_cache": len(IMAGE_RESPONSE_CACHE)
        },
        "thread_pools": {
            "io_executor": IO_EXECUTOR._max_workers,
            "image_executor": IMAGE_EXECUTOR._max_workers
        }
    }


# --- ДАШБОРДЫ ---
@app.get("/dashboards")
async def dashboards_list(request: Request, db: Session = Depends(get_db)):
    """Список дашбордов: админ видит все, остальные — только опубликованные."""
    try:
        is_admin = bool(request.session.get("dashboard_admin", False))

        def _load():
            q = db.query(models.Dashboard)
            if not is_admin:
                q = q.filter(models.Dashboard.is_published == True)
            items = q.order_by(models.Dashboard.updated_at.desc()).all()
            for dashboard in items:
                dashboard.elements = (
                    db.query(models.DashboardElement)
                    .filter(models.DashboardElement.dashboard_id == dashboard.id)
                    .all()
                )
            return items

        dashboards = await run_in_threadpool(_load)
        return templates.TemplateResponse("dashboards_list.html", {
            "request": request,
            "dashboards": dashboards,
            "session": request.session,
            "is_admin": is_admin,
        })
    except Exception as e:
        logger.exception("Ошибка в dashboards_list: %s", e)
        return templates.TemplateResponse("dashboards_list.html", {
            "request": request,
            "dashboards": [],
            "session": request.session,
            "is_admin": bool(request.session.get("dashboard_admin", False)),
        })


@app.get("/dashboard-admin/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request):
    if request.session.get("dashboard_admin"):
        return RedirectResponse("/dashboards", status_code=303)
    return templates.TemplateResponse("dashboard_login.html", {"request": request})


@app.post("/dashboard-admin/login")
async def dashboard_login(request: Request, access_code: str = Form(...)):
    if access_code == DASHBOARD_ADMIN_CODE:
        request.session["dashboard_admin"] = True
        return RedirectResponse("/dashboards", status_code=303)
    return templates.TemplateResponse("dashboard_login.html", {
        "request": request,
        "error": "Неверный код доступа"
    })


@app.get("/dashboard-admin/logout")
async def dashboard_logout(request: Request):
    request.session.pop("dashboard_admin", None)
    return RedirectResponse("/dashboards", status_code=303)


@app.get("/dashboard-admin/create")
async def create_dashboard_page(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("dashboard_admin"):
        return RedirectResponse("/dashboard-admin/login", status_code=303)
    return templates.TemplateResponse("dashboard_editor.html", {
        "request": request,
        "dashboard": None
    })


@app.post("/dashboard-admin/save")
async def save_dashboard(request: Request, db: Session = Depends(get_db)):
    """Сохранение черновика или публикации дашборда."""
    if not request.session.get("dashboard_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    try:
        data = await request.json()
        dashboard = await run_in_threadpool(
            lambda: dashboard_service.save_dashboard_payload(db, data)
        )
        return {
            "status": "success",
            "success": True,
            "id": dashboard.id,
            "slug": dashboard.slug,
            "published": bool(dashboard.is_published),
            "is_published": bool(dashboard.is_published),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Ошибка при сохранении дашборда: %s", e)
        await run_in_threadpool(db.rollback)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/dashboard-admin/publish/{dashboard_id}")
async def toggle_dashboard_publish(
    request: Request,
    dashboard_id: int,
    db: Session = Depends(get_db),
):
    """Переключение публикации без пересохранения виджетов."""
    if not request.session.get("dashboard_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    dashboard = await run_in_threadpool(
        lambda: db.query(models.Dashboard).filter(models.Dashboard.id == dashboard_id).first()
    )
    if not dashboard:
        raise HTTPException(status_code=404, detail="Дашборд не найден")

    try:
        payload = await request.json()
        if isinstance(payload, dict) and "is_published" in payload:
            dashboard.is_published = bool(payload["is_published"])
        else:
            dashboard.is_published = not bool(dashboard.is_published)
    except Exception:
        dashboard.is_published = not bool(dashboard.is_published)

    dashboard.updated_at = datetime.utcnow()
    await run_in_threadpool(db.commit)
    return {
        "status": "success",
        "success": True,
        "id": dashboard.id,
        "slug": dashboard.slug,
        "is_published": bool(dashboard.is_published),
    }


@app.get("/dashboard-admin/edit/{dashboard_id}")
async def edit_dashboard(request: Request, dashboard_id: int, db: Session = Depends(get_db)):
    if not request.session.get("dashboard_admin"):
        return RedirectResponse("/dashboard-admin/login", status_code=303)

    def _load():
        dashboard = db.query(models.Dashboard).filter(models.Dashboard.id == dashboard_id).first()
        if not dashboard:
            return None
        elements = (
            db.query(models.DashboardElement)
            .filter(models.DashboardElement.dashboard_id == dashboard_id)
            .order_by(models.DashboardElement.order_index)
            .all()
        )
        return dashboard_service.serialize_dashboard(dashboard, elements)

    dashboard_data = await run_in_threadpool(_load)
    if not dashboard_data:
        raise HTTPException(status_code=404, detail="Дашборд не найден")

    return templates.TemplateResponse("dashboard_editor.html", {
        "request": request,
        "dashboard": dashboard_data
    })


@app.post("/dashboard-admin/delete/{dashboard_id}")
async def delete_dashboard(request: Request, dashboard_id: int, db: Session = Depends(get_db)):
    if not request.session.get("dashboard_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    def _delete():
        dashboard = db.query(models.Dashboard).filter(models.Dashboard.id == dashboard_id).first()
        if not dashboard:
            return False
        db.delete(dashboard)
        db.commit()
        return True

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail="Дашборд не найден")
    return {"status": "success", "success": True}


@app.get("/dashboard/{slug}")
async def view_dashboard(request: Request, slug: str, db: Session = Depends(get_db)):
    """Публичный (или админский черновик) просмотр дашборда."""
    is_admin = bool(request.session.get("dashboard_admin"))

    def _load():
        if slug.isdigit():
            dashboard = db.query(models.Dashboard).filter(models.Dashboard.id == int(slug)).first()
        else:
            dashboard = db.query(models.Dashboard).filter(models.Dashboard.slug == slug).first()
        if not dashboard:
            return None
        if not dashboard.is_published and not is_admin:
            return None
        elements = (
            db.query(models.DashboardElement)
            .filter(models.DashboardElement.dashboard_id == dashboard.id)
            .order_by(models.DashboardElement.order_index)
            .all()
        )
        for element in elements:
            dashboard_service.parse_element_json_fields(element)
        dashboard.elements = elements
        return dashboard

    dashboard = await run_in_threadpool(_load)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Дашборд не найден")

    elements_payload = [
        {
            "id": el.id,
            "type": el.element_type,
            "chartType": el.chart_type,
            "title": el.title or "",
            "content": el.content if isinstance(el.content, dict) else {},
            "settings": el.settings if isinstance(el.settings, dict) else {},
            "options": (el.settings or {}).get("options") if isinstance(el.settings, dict) else None,
            "position": {"x": el.position_x or 0, "y": el.position_y or 0},
            "size": {"w": el.width or 4, "h": el.height or 3},
        }
        for el in dashboard.elements
    ]

    return templates.TemplateResponse("dashboard_view.html", {
        "request": request,
        "dashboard": dashboard,
        "session": request.session,
        "is_admin": is_admin,
        "elements_json": elements_payload,
    })


# --- БИБЛИОТЕКА ЗНАНИЙ ПО ПИТАНИЮ ---
KNOWLEDGE_BASE_ADMIN_CODE = "admin3377%"

DOCUMENT_TYPES = {
    "document": "Документ",
    "instruction": "Инструкция",
    "order": "Приказ",
    "method": "Методичка",
    "presentation": "Презентация",
    "video": "Видео",
    "spreadsheet": "Таблица",
    "image": "Изображение",
    "other": "Другое"
}

CATEGORY_ICONS = ["📁", "📊", "📋", "📌", "📚", "🎥", "📝", "⚖️", "🍎", "🥗", "📈", "🔬", "🏫", "👨‍🍳"]


@app.get("/knowledge-base", response_class=HTMLResponse)
async def knowledge_base(
        request: Request,
        category: int = None,
        search: str = "",
        page: int = 1,
        per_page: int = 12,
        sort: str = "newest",
        kb_db: Session = Depends(get_kb_db)
):
    """Главная страница библиотеки знаний"""

    # Получаем все активные категории
    categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(
            KnowledgeBaseCategory.is_active == True
        ).order_by(KnowledgeBaseCategory.order_index).all()
    )

    # Базовый запрос для документов
    query = kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.is_published == True)

    # Фильтр по категории
    if category:
        query = query.filter(KnowledgeBaseDocument.category_id == category)
        current_category = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseCategory).filter(KnowledgeBaseCategory.id == category).first()
        )
    else:
        current_category = None

    # Поиск
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                KnowledgeBaseDocument.title.ilike(search_term),
                KnowledgeBaseDocument.description.ilike(search_term),
                KnowledgeBaseDocument.tags.ilike(search_term)
            )
        )

        # Логируем поиск
        user_email = request.session.get("user_email")
        search_log = KnowledgeBaseSearchLog(
            query=search,
            user_email=user_email
        )
        kb_db.add(search_log)
        await run_in_threadpool(kb_db.commit)

    # Сортировка
    if sort == "newest":
        query = query.order_by(KnowledgeBaseDocument.created_at.desc())
    elif sort == "popular":
        query = query.order_by(KnowledgeBaseDocument.downloads_count.desc())
    elif sort == "views":
        query = query.order_by(KnowledgeBaseDocument.views_count.desc())
    elif sort == "title":
        query = query.order_by(KnowledgeBaseDocument.title)

    # Пагинация
    total = await run_in_threadpool(query.count)
    offset = (page - 1) * per_page
    documents = await run_in_threadpool(
        lambda: query.offset(offset).limit(per_page).all()
    )

    # Получаем популярные документы
    popular_docs = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.is_published == True
        ).order_by(KnowledgeBaseDocument.downloads_count.desc()).limit(5).all()
    )

    # Недавно добавленные
    recent_docs = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.is_published == True
        ).order_by(KnowledgeBaseDocument.created_at.desc()).limit(5).all()
    )

    # Получаем email пользователя из сессии
    user_email = request.session.get("user_email")
    favorites = []

    if user_email:
        favs = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseFavorite).filter(
                KnowledgeBaseFavorite.user_email == user_email
            ).all()
        )
        favorites = [fav.document_id for fav in favs]

    return templates.TemplateResponse("knowledge_base.html", {
        "request": request,
        "user_email": user_email,
        "categories": categories,
        "documents": documents,
        "popular_docs": popular_docs,
        "recent_docs": recent_docs,
        "current_category": current_category,
        "favorites": favorites,
        "total": total,
        "page": page,
        "per_page": per_page,
        "search": search,
        "sort": sort,
        "document_types": DOCUMENT_TYPES,
        "total_pages": (total + per_page - 1) // per_page
    })


@app.get("/knowledge-base/admin/login", response_class=HTMLResponse)
async def knowledge_base_admin_login(request: Request):
    """Страница входа в админку библиотеки"""
    return templates.TemplateResponse("knowledge_base_admin_login.html", {"request": request})


@app.post("/knowledge-base/admin/login")
async def knowledge_base_admin_login_post(
        request: Request,
        access_code: str = Form(...),
        email: str = Form(...),
        name: str = Form(""),
        kb_db: Session = Depends(get_kb_db)
):
    """Вход в админку библиотеки"""
    if access_code == KNOWLEDGE_BASE_ADMIN_CODE:
        # Сохраняем в сессии
        request.session["knowledge_base_admin"] = True
        request.session["admin_email"] = email
        request.session["admin_name"] = name if name else "Администратор"

        # Сохраняем/обновляем в БД
        admin = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseAdmin).filter(
                KnowledgeBaseAdmin.email == email
            ).first()
        )

        if not admin:
            admin = KnowledgeBaseAdmin(
                email=email,
                name=name if name else "Администратор",
                access_code=hashlib.sha256(access_code.encode()).hexdigest(),
                last_login=datetime.utcnow()
            )
            kb_db.add(admin)
        else:
            admin.last_login = datetime.utcnow()

        await run_in_threadpool(kb_db.commit)

        return RedirectResponse("/knowledge-base/admin", status_code=303)

    return templates.TemplateResponse("knowledge_base_admin_login.html", {
        "request": request,
        "error": "Неверный код доступа"
    })


@app.get("/knowledge-base/admin/logout")
async def knowledge_base_admin_logout(request: Request):
    """Выход из админки библиотеки"""
    request.session.pop("knowledge_base_admin", None)
    request.session.pop("admin_email", None)
    request.session.pop("admin_name", None)
    return RedirectResponse("/knowledge-base", status_code=303)


@app.get("/knowledge-base/admin", response_class=HTMLResponse)
async def knowledge_base_admin_panel(
        request: Request,
        kb_db: Session = Depends(get_kb_db)
):
    """Админ-панель библиотеки знаний"""
    if not request.session.get("knowledge_base_admin"):
        return RedirectResponse("/knowledge-base/admin/login", status_code=303)

    admin_email = request.session.get("admin_email")
    admin_name = request.session.get("admin_name")

    # Статистика
    total_docs = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).count()
    )
    total_categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).count()
    )
    total_downloads = await run_in_threadpool(
        lambda: kb_db.query(func.sum(KnowledgeBaseDocument.downloads_count)).scalar() or 0
    )
    total_views = await run_in_threadpool(
        lambda: kb_db.query(func.sum(KnowledgeBaseDocument.views_count)).scalar() or 0
    )

    # Последние загруженные
    recent_docs = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).order_by(
            KnowledgeBaseDocument.created_at.desc()
        ).limit(10).all()
    )

    # Категории с количеством документов
    categories_stats = await run_in_threadpool(
        lambda: kb_db.query(
            KnowledgeBaseCategory,
            func.count(KnowledgeBaseDocument.id).label('doc_count')
        ).outerjoin(
            KnowledgeBaseDocument,
            KnowledgeBaseCategory.id == KnowledgeBaseDocument.category_id
        ).group_by(KnowledgeBaseCategory.id).order_by(KnowledgeBaseCategory.order_index).all()
    )

    return templates.TemplateResponse("knowledge_base_admin.html", {
        "request": request,
        "admin_email": admin_email,
        "admin_name": admin_name,
        "total_docs": total_docs,
        "total_categories": total_categories,
        "total_downloads": total_downloads,
        "total_views": total_views,
        "recent_docs": recent_docs,
        "categories_stats": categories_stats
    })


@app.get("/knowledge-base/admin/categories", response_class=HTMLResponse)
async def manage_categories(
        request: Request,
        kb_db: Session = Depends(get_kb_db)
):
    """Управление категориями"""
    if not request.session.get("knowledge_base_admin"):
        return RedirectResponse("/knowledge-base/admin/login", status_code=303)

    categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).order_by(
            KnowledgeBaseCategory.order_index
        ).all()
    )

    return templates.TemplateResponse("knowledge_base_categories.html", {
        "request": request,
        "categories": categories,
        "icons": CATEGORY_ICONS
    })


@app.post("/knowledge-base/admin/category/create")
async def create_category(
        request: Request,
        name: str = Form(...),
        description: str = Form(""),
        icon: str = Form("📁"),
        color: str = Form("#667eea"),
        order_index: int = Form(0),
        kb_db: Session = Depends(get_kb_db)
):
    """Создание новой категории"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = KnowledgeBaseCategory(
        name=name,
        description=description,
        icon=icon,
        color=color,
        order_index=order_index
    )

    kb_db.add(category)
    await run_in_threadpool(kb_db.commit)

    return RedirectResponse("/knowledge-base/admin/categories", status_code=303)


@app.post("/knowledge-base/admin/category/{category_id}/update")
async def update_category(
        request: Request,
        category_id: int,
        name: str = Form(...),
        description: str = Form(""),
        icon: str = Form("📁"),
        color: str = Form("#667eea"),
        order_index: int = Form(0),
        is_active: bool = Form(True),
        kb_db: Session = Depends(get_kb_db)
):
    """Обновление категории"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(KnowledgeBaseCategory.id == category_id).first()
    )

    if not category:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    category.name = name
    category.description = description
    category.icon = icon
    category.color = color
    category.order_index = order_index
    category.is_active = is_active

    await run_in_threadpool(kb_db.commit)

    return RedirectResponse("/knowledge-base/admin/categories", status_code=303)


@app.post("/knowledge-base/admin/category/{category_id}/delete")
async def delete_category(
        request: Request,
        category_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Удаление категории"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(KnowledgeBaseCategory.id == category_id).first()
    )

    if category:
        await run_in_threadpool(lambda: kb_db.delete(category))
        await run_in_threadpool(kb_db.commit)

    return RedirectResponse("/knowledge-base/admin/categories", status_code=303)


@app.get("/knowledge-base/admin/upload", response_class=HTMLResponse)
async def upload_document_page(
        request: Request,
        kb_db: Session = Depends(get_kb_db)
):
    """Страница загрузки документа"""
    if not request.session.get("knowledge_base_admin"):
        return RedirectResponse("/knowledge-base/admin/login", status_code=303)

    categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(
            KnowledgeBaseCategory.is_active == True
        ).order_by(KnowledgeBaseCategory.order_index).all()
    )

    admin_name = request.session.get("admin_name", "Администратор")
    admin_email = request.session.get("admin_email", "")

    return templates.TemplateResponse("knowledge_base_upload.html", {
        "request": request,
        "categories": categories,
        "document_types": DOCUMENT_TYPES,
        "admin_name": admin_name,
        "admin_email": admin_email
    })


@app.post("/knowledge-base/admin/upload")
async def upload_document(
        request: Request,
        title: str = Form(...),
        description: str = Form(""),
        category_id: int = Form(None),
        document_type: str = Form("document"),
        tags: str = Form(""),
        is_featured: bool = Form(False),
        file: UploadFile = File(...),
        cover_image: UploadFile = File(None),
        kb_db: Session = Depends(get_kb_db)
):
    """Загрузка документа в библиотеку"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    admin_name = request.session.get("admin_name", "Администратор")
    admin_email = request.session.get("admin_email", "")

    # Создаём директории для файлов библиотеки
    BASE_DIR = Path(__file__).resolve().parent
    kb_files_dir = BASE_DIR / "knowledge_base_files"
    documents_dir = kb_files_dir / "documents"
    covers_dir = kb_files_dir / "covers"

    await run_in_threadpool(lambda: documents_dir.mkdir(parents=True, exist_ok=True))
    await run_in_threadpool(lambda: covers_dir.mkdir(parents=True, exist_ok=True))

    # Сохраняем основной файл
    file_ext = Path(file.filename).suffix.lower()
    safe_filename = f"{int(time.time())}_{secrets.token_hex(8)}{file_ext}"
    file_path = documents_dir / safe_filename

    await save_uploaded_file_optimized(file, file_path)

    # Сохраняем обложку (если есть)
    cover_path = None
    if cover_image and cover_image.filename:
        cover_ext = Path(cover_image.filename).suffix.lower()
        cover_filename = f"cover_{int(time.time())}_{secrets.token_hex(8)}{cover_ext}"
        cover_path = covers_dir / cover_filename
        await save_uploaded_file_optimized(cover_image, cover_path)

    # Создаем запись в отдельной БД
    document = KnowledgeBaseDocument(
        title=title,
        description=description,
        category_id=category_id if category_id else None,
        document_type=document_type,
        file_extension=file_ext,
        file_size=file.size,
        file_path=str(file_path.relative_to(BASE_DIR)),
        cover_image_path=str(cover_path.relative_to(BASE_DIR)) if cover_path else None,
        tags=tags,
        uploaded_by=admin_name,
        uploaded_by_email=admin_email,
        is_featured=is_featured
    )

    kb_db.add(document)
    await run_in_threadpool(kb_db.commit)

    return RedirectResponse(f"/knowledge-base/document/{document.id}", status_code=303)


@app.get("/knowledge-base/document/{doc_id}", response_class=HTMLResponse)
async def view_document(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Просмотр документа"""
    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document or not document.is_published:
        # Проверяем, может админ смотрит
        if not request.session.get("knowledge_base_admin"):
            raise HTTPException(status_code=404, detail="Документ не найден")

    # Увеличиваем счетчик просмотров
    document.views_count += 1
    await run_in_threadpool(kb_db.commit)

    # Похожие документы
    similar_docs = []
    if document.category_id:
        similar_docs = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseDocument).filter(
                KnowledgeBaseDocument.category_id == document.category_id,
                KnowledgeBaseDocument.id != doc_id,
                KnowledgeBaseDocument.is_published == True
            ).order_by(KnowledgeBaseDocument.downloads_count.desc()).limit(4).all()
        )

    # Категория
    category = None
    if document.category_id:
        category = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseCategory).filter(
                KnowledgeBaseCategory.id == document.category_id
            ).first()
        )

    # Проверяем избранное
    user_email = request.session.get("user_email")
    is_favorite = False

    if user_email:
        fav = await run_in_threadpool(
            lambda: kb_db.query(KnowledgeBaseFavorite).filter(
                KnowledgeBaseFavorite.user_email == user_email,
                KnowledgeBaseFavorite.document_id == doc_id
            ).first()
        )
        is_favorite = fav is not None

    # Получаем комментарии
    comments = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseComment).filter(
            KnowledgeBaseComment.document_id == doc_id,
            KnowledgeBaseComment.is_approved == True
        ).order_by(KnowledgeBaseComment.created_at.desc()).all()
    )

    return templates.TemplateResponse("knowledge_base_document.html", {
        "request": request,
        "document": document,
        "category": category,
        "similar_docs": similar_docs,
        "is_favorite": is_favorite,
        "comments": comments,
        "user_email": user_email,
        "document_types": DOCUMENT_TYPES,
        "is_admin": request.session.get("knowledge_base_admin", False)
    })


@app.get("/knowledge-base/download/{doc_id}")
async def download_document(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Скачивание документа"""
    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    # Проверяем опубликован ли документ (админы могут скачивать и неопубликованные)
    if not document.is_published and not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=404, detail="Документ не найден")

    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / document.file_path

    if not await run_in_threadpool(file_path.exists):
        raise HTTPException(status_code=404, detail="Файл не найден")

    # Увеличиваем счетчик скачиваний
    document.downloads_count += 1
    await run_in_threadpool(kb_db.commit)

    # Формируем имя файла для скачивания
    filename = f"{document.title}{document.file_extension}"

    # Кодируем имя файла для корректной обработки русских символов
    import urllib.parse
    encoded_filename = urllib.parse.quote(filename)

    # Возвращаем файл с правильными заголовками
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )


@app.get("/knowledge-base/cover/{doc_id}")
async def get_document_cover(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Обложка документа для карточек и страницы просмотра"""
    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document or not document.cover_image_path:
        raise HTTPException(status_code=404, detail="Обложка не найдена")

    if not document.is_published and not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=404, detail="Обложка не найдена")

    BASE_DIR = Path(__file__).resolve().parent
    cover_path = BASE_DIR / document.cover_image_path

    if not await run_in_threadpool(cover_path.exists):
        raise HTTPException(status_code=404, detail="Обложка не найдена")

    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(cover_path.suffix.lower(), "image/jpeg")

    return FileResponse(
        path=cover_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"}
    )


@app.post("/knowledge-base/favorite/{doc_id}")
async def toggle_favorite(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Добавить/удалить из избранного"""
    user_email = request.session.get("user_email")
    if not user_email:
        return {"status": "error", "message": "Требуется авторизация"}

    favorite = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseFavorite).filter(
            KnowledgeBaseFavorite.user_email == user_email,
            KnowledgeBaseFavorite.document_id == doc_id
        ).first()
    )

    if favorite:
        await run_in_threadpool(lambda: kb_db.delete(favorite))
        await run_in_threadpool(kb_db.commit)
        return {"status": "success", "action": "removed"}
    else:
        new_favorite = KnowledgeBaseFavorite(
            user_email=user_email,
            document_id=doc_id
        )
        kb_db.add(new_favorite)
        await run_in_threadpool(kb_db.commit)
        return {"status": "success", "action": "added"}


@app.post("/knowledge-base/comment/{doc_id}")
async def add_comment(
        request: Request,
        doc_id: int,
        content: str = Form(...),
        user_name: str = Form(""),
        kb_db: Session = Depends(get_kb_db)
):
    """Добавление комментария"""
    user_email = request.session.get("user_email")

    comment = KnowledgeBaseComment(
        document_id=doc_id,
        user_name=user_name if user_name else "Гость",
        user_email=user_email,
        content=content,
        is_approved=False  # Требуется модерация
    )

    kb_db.add(comment)
    await run_in_threadpool(kb_db.commit)

    return RedirectResponse(f"/knowledge-base/document/{doc_id}", status_code=303)


@app.get("/knowledge-base/admin/edit/{doc_id}", response_class=HTMLResponse)
async def edit_document_page(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Редактирование документа"""
    if not request.session.get("knowledge_base_admin"):
        return RedirectResponse("/knowledge-base/admin/login", status_code=303)

    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(
            KnowledgeBaseCategory.is_active == True
        ).order_by(KnowledgeBaseCategory.order_index).all()
    )

    return templates.TemplateResponse("knowledge_base_edit.html", {
        "request": request,
        "document": document,
        "categories": categories,
        "document_types": DOCUMENT_TYPES
    })


@app.post("/knowledge-base/admin/edit/{doc_id}")
async def edit_document(
        request: Request,
        doc_id: int,
        title: str = Form(...),
        description: str = Form(""),
        category_id: int = Form(None),
        document_type: str = Form("document"),
        tags: str = Form(""),
        is_published: bool = Form(True),
        is_featured: bool = Form(False),
        kb_db: Session = Depends(get_kb_db)
):
    """Обновление документа"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    document.title = title
    document.description = description
    document.category_id = category_id if category_id else None
    document.document_type = document_type
    document.tags = tags
    document.is_published = is_published
    document.is_featured = is_featured
    document.updated_at = datetime.utcnow()

    await run_in_threadpool(kb_db.commit)

    return RedirectResponse(f"/knowledge-base/document/{doc_id}", status_code=303)


@app.post("/knowledge-base/admin/delete/{doc_id}")
async def delete_document(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """Удаление документа"""
    if not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    # Удаляем файлы
    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / document.file_path
    await delete_file_optimized(file_path)

    if document.cover_image_path:
        cover_path = BASE_DIR / document.cover_image_path
        await delete_file_optimized(cover_path)

    # Удаляем запись из БД
    await run_in_threadpool(lambda: kb_db.delete(document))
    await run_in_threadpool(kb_db.commit)

    return RedirectResponse("/knowledge-base/admin", status_code=303)


@app.get("/knowledge-base/api/search")
async def knowledge_base_search_api(
        request: Request,
        q: str = "",
        kb_db: Session = Depends(get_kb_db)
):
    """API для быстрого поиска"""
    if len(q) < 2:
        return {"results": []}

    search_term = f"%{q}%"
    results = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.is_published == True,
            or_(
                KnowledgeBaseDocument.title.ilike(search_term),
                KnowledgeBaseDocument.description.ilike(search_term),
                KnowledgeBaseDocument.tags.ilike(search_term)
            )
        ).limit(10).all()
    )

    return {
        "results": [
            {
                "id": doc.id,
                "title": doc.title,
                "type": DOCUMENT_TYPES.get(doc.document_type, "Документ"),
                "url": f"/knowledge-base/document/{doc.id}",
                "cover_url": f"/knowledge-base/cover/{doc.id}" if doc.cover_image_path else None,
            }
            for doc in results
        ]
    }


@app.get("/knowledge-base/stats")
async def knowledge_base_stats(
        request: Request,
        kb_db: Session = Depends(get_kb_db)
):
    """Публичная статистика библиотеки"""
    total_docs = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.is_published == True
        ).count()
    )

    total_downloads = await run_in_threadpool(
        lambda: kb_db.query(func.sum(KnowledgeBaseDocument.downloads_count)).scalar() or 0
    )

    # Топ категорий
    top_categories = await run_in_threadpool(
        lambda: kb_db.query(
            KnowledgeBaseCategory.name,
            KnowledgeBaseCategory.icon,
            func.count(KnowledgeBaseDocument.id).label('doc_count')
        ).join(
            KnowledgeBaseDocument,
            KnowledgeBaseCategory.id == KnowledgeBaseDocument.category_id
        ).filter(
            KnowledgeBaseDocument.is_published == True
        ).group_by(KnowledgeBaseCategory.id).order_by(func.count(KnowledgeBaseDocument.id).desc()).limit(5).all()
    )

    return templates.TemplateResponse("knowledge_base_stats.html", {
        "request": request,
        "total_docs": total_docs,
        "total_downloads": total_downloads,
        "top_categories": top_categories
    })


def _kb_ai_client_key(request: Request) -> str:
    user = request.session.get("user_email") or request.session.get("admin_email")
    if user:
        return f"user:{user}"
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return f"ip:{ip}"


def _kb_ai_rate_limit(request: Request, limit: int = 25) -> None:
    key = _kb_ai_client_key(request)
    count = KB_AI_RATE_CACHE.get(key, 0) + 1
    KB_AI_RATE_CACHE[key] = count
    if count > limit:
        raise HTTPException(status_code=429, detail="Слишком много запросов к ИИ. Подождите несколько минут.")


@app.post("/knowledge-base/ai/search")
async def knowledge_base_ai_search(
        request: Request,
        kb_db: Session = Depends(get_kb_db)
):
    """ИИ-помощник: поиск подходящих документов по запросу пользователя"""
    if not kb_ai_service.is_configured():
        raise HTTPException(status_code=503, detail="ИИ-помощник не настроен (нет OPENROUTER_API_KEY)")

    _kb_ai_rate_limit(request)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный JSON")

    message = (payload.get("message") or "").strip()
    if len(message) < 2:
        raise HTTPException(status_code=400, detail="Введите запрос")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Слишком длинный запрос")

    history = payload.get("history") or []
    if not isinstance(history, list):
        history = []

    documents = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.is_published == True
        ).order_by(KnowledgeBaseDocument.downloads_count.desc()).all()
    )
    categories = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseCategory).filter(
            KnowledgeBaseCategory.is_active == True
        ).all()
    )
    category_map = {c.id: c.name for c in categories}
    base_dir = Path(__file__).resolve().parent

    try:
        result = await kb_ai_service.search_documents_with_ai(
            user_message=message,
            documents=documents,
            base_dir=base_dir,
            document_types=DOCUMENT_TYPES,
            categories=category_map,
            history=history,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        logging.exception("KB AI search failed")
        raise HTTPException(status_code=500, detail="Ошибка ИИ-помощника")

    return result


@app.post("/knowledge-base/ai/document/{doc_id}")
async def knowledge_base_ai_document(
        request: Request,
        doc_id: int,
        kb_db: Session = Depends(get_kb_db)
):
    """ИИ-выжимка и ответы по конкретному документу"""
    if not kb_ai_service.is_configured():
        raise HTTPException(status_code=503, detail="ИИ-помощник не настроен (нет OPENROUTER_API_KEY)")

    _kb_ai_rate_limit(request)

    document = await run_in_threadpool(
        lambda: kb_db.query(KnowledgeBaseDocument).filter(KnowledgeBaseDocument.id == doc_id).first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if not document.is_published and not request.session.get("knowledge_base_admin"):
        raise HTTPException(status_code=404, detail="Документ не найден")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    question = (payload.get("question") or "").strip()
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="Слишком длинный вопрос")

    history = payload.get("history") or []
    if not isinstance(history, list):
        history = []

    base_dir = Path(__file__).resolve().parent

    try:
        result = await kb_ai_service.ask_about_document(
            document=document,
            base_dir=base_dir,
            document_types=DOCUMENT_TYPES,
            question=question or None,
            history=history,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        logging.exception("KB AI document failed")
        raise HTTPException(status_code=500, detail="Ошибка ИИ-помощника")

    return result


# ========== УНИВЕРСАЛЬНАЯ СИСТЕМА УПРАВЛЕНИЯ ОТЧЁТНОСТЬЮ ==========
# Константа для регионального доступа
REGIONAL_REPORT_CODE = "MoinCHR3377"

# Создаём папку для файлов отчётов при старте приложения
REPORTS_DIR = Path(__file__).resolve().parent / "reports_files"
REPORTS_DIR.mkdir(exist_ok=True)


@app.get("/regional-admin/login", response_class=HTMLResponse)
async def regional_admin_login_page(request: Request):
    """Страница входа в региональную систему отчётности"""
    return templates.TemplateResponse("regional_admin_login.html", {"request": request})


@app.post("/regional-admin/login")
async def regional_admin_login(request: Request, access_code: str = Form(...)):
    """Вход в региональную систему отчётности"""
    if access_code == REGIONAL_REPORT_CODE:
        request.session["regional_admin"] = True
        request.session["regional_admin_login_time"] = datetime.now().isoformat()
        return RedirectResponse("/regional-admin/dashboard", status_code=303)

    return templates.TemplateResponse("regional_admin_login.html", {
        "request": request,
        "error": "Неверный код доступа"
    })


@app.get("/regional-admin/logout")
async def regional_admin_logout(request: Request):
    """Выход из региональной системы отчётности"""
    request.session.pop("regional_admin", None)
    return RedirectResponse("/regional-admin/login", status_code=303)


@app.get("/regional-admin/dashboard", response_class=HTMLResponse)
async def regional_admin_dashboard(
        request: Request,
        db: Session = Depends(get_db)
):
    """Дашборд регионального администратора"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    # Статистика
    total_reports = await run_in_threadpool(lambda: db.query(models.Report).count())
    total_categories = await run_in_threadpool(lambda: db.query(models.ReportCategory).count())
    total_files = await run_in_threadpool(lambda: db.query(models.ReportFile).count())

    # Отчёты по статусам
    draft_count = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.status == "draft").count())
    published_count = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.is_published == True).count())
    archived_count = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.status == "archived").count())

    # Отчёты по годам
    years_stats = await run_in_threadpool(
        lambda: db.query(models.Report.year, func.count(models.Report.id))
        .group_by(models.Report.year)
        .order_by(models.Report.year.desc())
        .limit(5)
        .all()
    )

    # Последние 10 отчётов
    recent_reports = await run_in_threadpool(
        lambda: db.query(models.Report)
        .order_by(models.Report.created_at.desc())
        .limit(10)
        .all()
    )

    # Категории с количеством отчётов
    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory)
        .filter(models.ReportCategory.is_active == True)
        .order_by(models.ReportCategory.order_index)
        .all()
    )

    for cat in categories:
        cat.report_count = await run_in_threadpool(
            lambda: db.query(models.Report).filter(models.Report.category_id == cat.id).count()
        )

    return templates.TemplateResponse("regional_admin_dashboard.html", {
        "request": request,
        "total_reports": total_reports,
        "total_categories": total_categories,
        "total_files": total_files,
        "draft_count": draft_count,
        "published_count": published_count,
        "archived_count": archived_count,
        "years_stats": years_stats,
        "recent_reports": recent_reports,
        "categories": categories,
        "months": MONTHS
    })


@app.get("/regional-admin/categories", response_class=HTMLResponse)
async def regional_admin_categories(
        request: Request,
        db: Session = Depends(get_db)
):
    """Управление категориями отчётов"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory)
        .order_by(models.ReportCategory.order_index)
        .all()
    )

    for cat in categories:
        cat.report_count = await run_in_threadpool(
            lambda: db.query(models.Report).filter(models.Report.category_id == cat.id).count()
        )

    return templates.TemplateResponse("regional_admin_categories.html", {
        "request": request,
        "categories": categories
    })


@app.post("/regional-admin/category/create")
async def regional_admin_create_category(
        request: Request,
        name: str = Form(...),
        description: str = Form(""),
        icon: str = Form("📊"),
        color: str = Form("#667eea"),
        parent_id: int = Form(None),
        order_index: int = Form(0),
        db: Session = Depends(get_db)
):
    """Создание категории отчётов"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = models.ReportCategory(
        name=name,
        description=description,
        icon=icon,
        color=color,
        parent_id=parent_id if parent_id else None,
        order_index=order_index
    )

    db.add(category)
    await run_in_threadpool(db.commit)

    return RedirectResponse("/regional-admin/categories", status_code=303)


@app.post("/regional-admin/category/{category_id}/update")
async def regional_admin_update_category(
        request: Request,
        category_id: int,
        name: str = Form(...),
        description: str = Form(""),
        icon: str = Form("📊"),
        color: str = Form("#667eea"),
        order_index: int = Form(0),
        is_active: bool = Form(True),
        db: Session = Depends(get_db)
):
    """Обновление категории"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.id == category_id).first()
    )

    if category:
        category.name = name
        category.description = description
        category.icon = icon
        category.color = color
        category.order_index = order_index
        category.is_active = is_active
        await run_in_threadpool(db.commit)

    return RedirectResponse("/regional-admin/categories", status_code=303)


@app.post("/regional-admin/category/{category_id}/delete")
async def regional_admin_delete_category(
        request: Request,
        category_id: int,
        db: Session = Depends(get_db)
):
    """Удаление категории"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    category = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.id == category_id).first()
    )

    if category:
        await run_in_threadpool(lambda: db.delete(category))
        await run_in_threadpool(db.commit)

    return RedirectResponse("/regional-admin/categories", status_code=303)


@app.get("/regional-admin/reports", response_class=HTMLResponse)
async def regional_admin_reports(
        request: Request,
        category_id: str = None,  # Изменяем на str, чтобы принимать пустые строки
        year: str = None,  # Изменяем на str
        status: str = None,
        search: str = "",
        page: int = 1,
        per_page: int = 20,
        db: Session = Depends(get_db)
):
    """Список отчётов"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    query = db.query(models.Report)

    # Преобразуем строки в числа, если они не пустые
    category_id_int = None
    if category_id and category_id.strip():
        try:
            category_id_int = int(category_id)
        except ValueError:
            pass

    year_int = None
    if year and year.strip():
        try:
            year_int = int(year)
        except ValueError:
            pass

    # Применяем фильтры
    if category_id_int:
        query = query.filter(models.Report.category_id == category_id_int)
    if year_int:
        query = query.filter(models.Report.year == year_int)
    if status:
        query = query.filter(models.Report.status == status)
    if search:
        query = query.filter(
            or_(
                models.Report.title.ilike(f"%{search}%"),
                models.Report.description.ilike(f"%{search}%")
            )
        )

    total = await run_in_threadpool(query.count)
    offset = (page - 1) * per_page
    reports = await run_in_threadpool(
        lambda: query.order_by(models.Report.created_at.desc())
        .offset(offset).limit(per_page).all()
    )

    # Получаем категории для фильтра
    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.is_active == True).all()
    )

    # Получаем доступные годы
    years_result = await run_in_threadpool(
        lambda: db.query(models.Report.year).distinct().order_by(models.Report.year.desc()).all()
    )
    years = [str(y[0]) for y in years_result if y[0]]

    return templates.TemplateResponse("regional_admin_reports.html", {
        "request": request,
        "reports": reports,
        "total": total,
        "page": page,
        "per_page": per_page,
        "categories": categories,
        "years": years,
        "selected_category": category_id if category_id else "",
        "selected_year": year if year else "",
        "selected_status": status if status else "",
        "search": search
    })


@app.get("/regional-admin/report/create", response_class=HTMLResponse)
async def regional_admin_create_report_page(
        request: Request,
        db: Session = Depends(get_db)
):
    """Страница создания отчёта"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.is_active == True).all()
    )

    # Доступные типы отчётов
    report_types = [
        {"value": "hot_meal", "name": "Горячее питание", "icon": "🍲",
         "description": "Отчёты по организации горячего питания"},
        {"value": "salary", "name": "Зарплата педработников", "icon": "💰",
         "description": "Мониторинг трудовой нагрузки и доходов"},
        {"value": "accidents", "name": "Несчастные случаи", "icon": "⚠️", "description": "Отчёты о несчастных случаях"},
        {"value": "building", "name": "Перепрофилирование", "icon": "🏫",
         "description": "Перепрофилирование сооружений"},
        {"value": "cadet", "name": "Кадетское образование", "icon": "🎖️", "description": "Кадетские корпуса и классы"},
        {"value": "benefits", "name": "Льготы на питание", "icon": "🎁",
         "description": "Региональные и муниципальные льготы"},
        {"value": "custom", "name": "Произвольный отчёт", "icon": "📝",
         "description": "Создать отчёт с произвольными данными"}
    ]

    return templates.TemplateResponse("regional_admin_report_create.html", {
        "request": request,
        "categories": categories,
        "report_types": report_types,
        "months": MONTHS
    })


@app.post("/regional-admin/report/create")
async def regional_admin_create_report(
        request: Request,
        title: str = Form(...),
        description: str = Form(""),
        category_id: int = Form(None),
        report_type: str = Form("custom"),
        year: int = Form(...),
        month: int = Form(None),
        quarter: int = Form(None),
        report_data: str = Form("{}"),
        status: str = Form("draft"),
        files: List[UploadFile] = File(None),
        db: Session = Depends(get_db)
):
    """Создание нового отчёта"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    # Парсим данные отчёта
    try:
        if report_data and report_data.strip():
            data = json.loads(report_data)
        else:
            data = {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}, data: {report_data}")
        data = {}

    # Создаём отчёт
    report = models.Report(
        title=title,
        description=description,
        category_id=category_id if category_id else None,
        report_type=report_type,
        year=year,
        month=month,
        quarter=quarter,
        data=json.dumps(data, ensure_ascii=False),
        status=status,
        is_published=(status == "published")
    )

    db.add(report)
    await run_in_threadpool(db.flush)

    # Сохраняем прикреплённые файлы
    if files:
        for file in files:
            if file.filename:
                file_ext = Path(file.filename).suffix.lower()
                safe_name = f"report_{report.id}_{int(time.time())}_{secrets.token_hex(8)}{file_ext}"
                file_path = REPORTS_DIR / safe_name

                await save_uploaded_file_optimized(file, file_path)

                report_file = models.ReportFile(
                    report_id=report.id,
                    filename=safe_name,
                    original_name=file.filename,
                    file_path=str(file_path.relative_to(Path(__file__).resolve().parent)),
                    file_size=file.size,
                    file_type=file_ext[1:] if file_ext else "unknown"
                )
                db.add(report_file)

    await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report.id}", status_code=303)


@app.get("/regional-admin/report/{report_id}", response_class=HTMLResponse)
async def regional_admin_view_report(
        request: Request,
        report_id: int,
        db: Session = Depends(get_db)
):
    """Просмотр отчёта"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    # Парсим данные
    try:
        report.data = json.loads(report.data) if report.data else {}
    except:
        report.data = {}

    # Получаем файлы
    files = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(models.ReportFile.report_id == report_id).all()
    )

    # Получаем категорию
    category = None
    if report.category_id:
        category = await run_in_threadpool(
            lambda: db.query(models.ReportCategory).filter(models.ReportCategory.id == report.category_id).first()
        )

    # Получаем версии
    versions = await run_in_threadpool(
        lambda: db.query(models.ReportVersion).filter(models.ReportVersion.report_id == report_id).order_by(
            models.ReportVersion.version_number.desc()).all()
    )

    # Получаем комментарии
    comments = await run_in_threadpool(
        lambda: db.query(models.ReportComment).filter(models.ReportComment.report_id == report_id).order_by(
            models.ReportComment.created_at.desc()).all()
    )

    return templates.TemplateResponse("regional_admin_report_view.html", {
        "request": request,
        "report": report,
        "category": category,
        "files": files,
        "versions": versions,
        "comments": comments,
        "months": MONTHS
    })


@app.get("/regional-admin/report/{report_id}/edit", response_class=HTMLResponse)
async def regional_admin_edit_report_page(
        request: Request,
        report_id: int,
        db: Session = Depends(get_db)
):
    """Страница редактирования отчёта"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    # Парсим данные
    try:
        report.data = json.loads(report.data) if report.data else {}
    except:
        report.data = {}

    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.is_active == True).all()
    )

    files = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(models.ReportFile.report_id == report_id).all()
    )

    return templates.TemplateResponse("regional_admin_report_edit.html", {
        "request": request,
        "report": report,
        "categories": categories,
        "files": files,
        "months": MONTHS
    })


@app.post("/regional-admin/report/{report_id}/edit")
async def regional_admin_update_report(
        request: Request,
        report_id: int,
        title: str = Form(...),
        description: str = Form(""),
        category_id: int = Form(None),
        year: int = Form(...),
        month: int = Form(None),
        quarter: int = Form(None),
        report_data: str = Form("{}"),
        status: str = Form("draft"),
        db: Session = Depends(get_db)
):
    """Обновление отчёта"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    # Сохраняем текущую версию перед изменением
    current_version = models.ReportVersion(
        report_id=report.id,
        version_number=(await run_in_threadpool(
            lambda: db.query(models.ReportVersion).filter(models.ReportVersion.report_id == report_id).count()
        )) + 1,
        data_snapshot=json.dumps(report.data) if report.data else "{}",
        changed_at=datetime.utcnow(),
        change_comment="Автоматическое сохранение версии перед редактированием"
    )
    db.add(current_version)

    # Обновляем отчёт
    report.title = title
    report.description = description
    report.category_id = category_id if category_id else None
    report.year = year
    report.month = month
    report.quarter = quarter
    report.data = report_data
    report.status = status
    report.is_published = (status == "published")
    report.updated_at = datetime.utcnow()

    await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report_id}", status_code=303)


@app.post("/regional-admin/report/{report_id}/add-files")
async def regional_admin_add_report_files(
        request: Request,
        report_id: int,
        files: List[UploadFile] = File(...),
        db: Session = Depends(get_db)
):
    """Добавление файлов к отчёту"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    for file in files:
        if file.filename:
            file_ext = Path(file.filename).suffix.lower()
            safe_name = f"report_{report_id}_{int(time.time())}_{secrets.token_hex(8)}{file_ext}"
            file_path = REPORTS_DIR / safe_name

            await save_uploaded_file_optimized(file, file_path)

            report_file = models.ReportFile(
                report_id=report_id,
                filename=safe_name,
                original_name=file.filename,
                file_path=str(file_path.relative_to(Path(__file__).resolve().parent)),
                file_size=file.size,
                file_type=file_ext[1:] if file_ext else "unknown"
            )
            db.add(report_file)

    await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report_id}", status_code=303)


@app.get("/regional-admin/report/{report_id}/delete-file/{file_id}")
async def regional_admin_delete_report_file(
        request: Request,
        report_id: int,
        file_id: int,
        db: Session = Depends(get_db)
):
    """Удаление файла из отчёта"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    file = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(models.ReportFile.id == file_id,
                                                   models.ReportFile.report_id == report_id).first()
    )

    if file:
        # Удаляем физический файл
        BASE_DIR = Path(__file__).resolve().parent
        file_path = BASE_DIR / file.file_path
        await delete_file_optimized(file_path)

        # Удаляем запись из БД
        await run_in_threadpool(lambda: db.delete(file))
        await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report_id}", status_code=303)


@app.post("/regional-admin/report/{report_id}/delete")
async def regional_admin_delete_report(
        request: Request,
        report_id: int,
        db: Session = Depends(get_db)
):
    """Удаление отчёта"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if report:
        # Удаляем связанные файлы
        files = await run_in_threadpool(
            lambda: db.query(models.ReportFile).filter(models.ReportFile.report_id == report_id).all()
        )
        BASE_DIR = Path(__file__).resolve().parent
        for file in files:
            file_path = BASE_DIR / file.file_path
            await delete_file_optimized(file_path)

        # Удаляем отчёт (каскадно удалятся связанные записи)
        await run_in_threadpool(lambda: db.delete(report))
        await run_in_threadpool(db.commit)

    return RedirectResponse("/regional-admin/reports", status_code=303)


@app.post("/regional-admin/report/{report_id}/comment")
async def regional_admin_add_comment(
        request: Request,
        report_id: int,
        content: str = Form(...),
        db: Session = Depends(get_db)
):
    """Добавление комментария к отчёту"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    comment = models.ReportComment(
        report_id=report_id,
        user_name="Региональный администратор",
        content=content
    )

    db.add(comment)
    await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report_id}", status_code=303)


@app.get("/regional-admin/report/{report_id}/export/{format}")
async def regional_admin_export_report(
        request: Request,
        report_id: int,
        format: str,  # json, html
        db: Session = Depends(get_db)
):
    """Экспорт отчёта в разных форматах"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(models.Report.id == report_id).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    try:
        report.data = json.loads(report.data) if report.data else {}
    except:
        report.data = {}

    if format == "json":
        return JSONResponse({
            "id": report.id,
            "title": report.title,
            "description": report.description,
            "year": report.year,
            "month": report.month,
            "quarter": report.quarter,
            "data": report.data,
            "status": report.status,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "updated_at": report.updated_at.isoformat() if report.updated_at else None
        })

    elif format == "html":
        # Генерируем HTML страницу с отчётом
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{report.title}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
                h1 {{ color: #333; border-bottom: 2px solid #667eea; padding-bottom: 10px; }}
                .meta {{ color: #666; margin-bottom: 20px; }}
                .data-section {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                pre {{ background: #fff; padding: 15px; overflow-x: auto; }}
            </style>
        </head>
        <body>
            <h1>{report.title}</h1>
            <div class="meta">
                <strong>Год:</strong> {report.year} | 
                <strong>Статус:</strong> {report.status} |
                <strong>Создан:</strong> {report.created_at.strftime('%d.%m.%Y %H:%M') if report.created_at else '—'}
            </div>
            <p>{report.description or 'Нет описания'}</p>
            <div class="data-section">
                <h3>Данные отчёта</h3>
                <pre>{json.dumps(report.data, ensure_ascii=False, indent=2)}</pre>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    else:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат экспорта")


@app.get("/regional-admin/import-report-form", response_class=HTMLResponse)
async def regional_admin_import_report_form(request: Request, db: Session = Depends(get_db)):
    """Форма для импорта отчёта"""
    if not request.session.get("regional_admin"):
        return RedirectResponse("/regional-admin/login", status_code=303)

    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(models.ReportCategory.is_active == True).all()
    )

    return templates.TemplateResponse("regional_admin_import_report.html", {
        "request": request,
        "categories": categories
    })


@app.post("/regional-admin/import-report")
async def regional_admin_import_report(
        request: Request,
        title: str = Form(...),
        description: str = Form(""),
        category_id: int = Form(None),
        year: int = Form(...),
        month: int = Form(None),
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    """Импорт отчёта из файла (PDF, DOCX, XLSX, JSON)"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    # Определяем тип файла
    file_ext = Path(file.filename).suffix.lower()

    # Пытаемся извлечь данные из файла
    report_data = {}

    if file_ext == '.json':
        content = await file.read()
        try:
            report_data = json.loads(content.decode('utf-8'))
        except:
            pass

    # Создаём отчёт
    report = models.Report(
        title=title,
        description=description,
        category_id=category_id if category_id else None,
        year=year,
        month=month,
        data=json.dumps(report_data, ensure_ascii=False) if report_data else "{}",
        status="draft"
    )

    db.add(report)
    await run_in_threadpool(db.flush)

    # Сохраняем загруженный файл
    safe_name = f"import_{report.id}_{int(time.time())}_{secrets.token_hex(8)}{file_ext}"
    file_path = REPORTS_DIR / safe_name

    await save_uploaded_file_optimized(file, file_path)

    report_file = models.ReportFile(
        report_id=report.id,
        filename=safe_name,
        original_name=file.filename,
        file_path=str(file_path.relative_to(Path(__file__).resolve().parent)),
        file_size=file.size,
        file_type=file_ext[1:] if file_ext else "unknown"
    )
    db.add(report_file)

    await run_in_threadpool(db.commit)

    return RedirectResponse(f"/regional-admin/report/{report.id}", status_code=303)


# Добавляем ссылку на региональную админку в layout.html через контекстный процессор
@app.middleware("http")
async def add_regional_admin_link(request: Request, call_next):
    response = await call_next(request)
    return response


@app.get("/regional-admin/report/{report_id}/download-file/{file_id}")
async def regional_admin_download_report_file(
        request: Request,
        report_id: int,
        file_id: int,
        db: Session = Depends(get_db)
):
    """Скачивание файла из отчёта"""
    if not request.session.get("regional_admin"):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    file = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(
            models.ReportFile.id == file_id,
            models.ReportFile.report_id == report_id
        ).first()
    )

    if not file:
        raise HTTPException(status_code=404, detail="Файл не найден")

    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / file.file_path

    if not await run_in_threadpool(file_path.exists):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    # Кодируем имя файла для корректной обработки русских символов
    import urllib.parse
    encoded_filename = urllib.parse.quote(file.original_name)

    return FileResponse(
        path=file_path,
        filename=file.original_name,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )


# ========== ПУБЛИЧНЫЕ ОТЧЁТЫ ==========
@app.get("/public-reports", response_class=HTMLResponse)
async def public_reports(
        request: Request,
        category_id: str = None,
        year: str = None,
        search: str = "",
        page: int = 1,
        per_page: int = 20,
        db: Session = Depends(get_db)
):
    """Публичная страница с опубликованными отчётами"""

    query = db.query(models.Report).filter(
        models.Report.is_published == True,
        models.Report.status == "published"
    )

    # Преобразуем строки в числа, если они не пустые
    category_id_int = None
    if category_id and category_id.strip():
        try:
            category_id_int = int(category_id)
        except ValueError:
            pass

    year_int = None
    if year and year.strip():
        try:
            year_int = int(year)
        except ValueError:
            pass

    # Применяем фильтры
    if category_id_int:
        query = query.filter(models.Report.category_id == category_id_int)
    if year_int:
        query = query.filter(models.Report.year == year_int)
    if search:
        query = query.filter(
            or_(
                models.Report.title.ilike(f"%{search}%"),
                models.Report.description.ilike(f"%{search}%")
            )
        )

    total = await run_in_threadpool(query.count)
    offset = (page - 1) * per_page
    reports = await run_in_threadpool(
        lambda: query.order_by(models.Report.created_at.desc())
        .offset(offset).limit(per_page).all()
    )

    # Получаем категории для фильтра
    categories = await run_in_threadpool(
        lambda: db.query(models.ReportCategory).filter(
            models.ReportCategory.is_active == True
        ).all()
    )

    # Получаем доступные годы
    years_result = await run_in_threadpool(
        lambda: db.query(models.Report.year).distinct().order_by(models.Report.year.desc()).all()
    )
    years = [str(y[0]) for y in years_result if y[0]]

    # Загружаем данные отчётов
    for report in reports:
        try:
            report.data = json.loads(report.data) if report.data else {}
        except:
            report.data = {}

        if report.category_id:
            report.category = await run_in_threadpool(
                lambda: db.query(models.ReportCategory).filter(models.ReportCategory.id == report.category_id).first()
            )

    return templates.TemplateResponse("public_reports.html", {
        "request": request,
        "reports": reports,
        "total": total,
        "page": page,
        "per_page": per_page,
        "categories": categories,
        "years": years,
        "selected_category": category_id if category_id else "",
        "selected_year": year if year else "",
        "search": search
    })


@app.get("/public-reports/{report_id}", response_class=HTMLResponse)
async def public_report_detail(
        request: Request,
        report_id: int,
        db: Session = Depends(get_db)
):
    """Публичный просмотр отдельного отчёта"""

    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(
            models.Report.id == report_id,
            models.Report.is_published == True,
            models.Report.status == "published"
        ).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Отчёт не найден")

    # Парсим данные
    try:
        report.data = json.loads(report.data) if report.data else {}
    except:
        report.data = {}

    # Получаем категорию
    category = None
    if report.category_id:
        category = await run_in_threadpool(
            lambda: db.query(models.ReportCategory).filter(models.ReportCategory.id == report.category_id).first()
        )

    # Получаем файлы
    files = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(models.ReportFile.report_id == report_id).all()
    )

    return templates.TemplateResponse("public_report_detail.html", {
        "request": request,
        "report": report,
        "category": category,
        "files": files
    })


@app.get("/public-reports/download/{file_id}")
async def public_download_file(
        request: Request,
        file_id: int,
        db: Session = Depends(get_db)
):
    """Публичное скачивание файла из отчёта"""

    file = await run_in_threadpool(
        lambda: db.query(models.ReportFile).filter(models.ReportFile.id == file_id).first()
    )

    if not file:
        raise HTTPException(status_code=404, detail="Файл не найден")

    # Проверяем, что отчёт опубликован
    report = await run_in_threadpool(
        lambda: db.query(models.Report).filter(
            models.Report.id == file.report_id,
            models.Report.is_published == True,
            models.Report.status == "published"
        ).first()
    )

    if not report:
        raise HTTPException(status_code=404, detail="Файл не найден")

    BASE_DIR = Path(__file__).resolve().parent
    file_path = BASE_DIR / file.file_path

    if not await run_in_threadpool(file_path.exists):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    # Увеличиваем счётчик просмотров
    report.views_count = (report.views_count or 0) + 1
    await run_in_threadpool(db.commit)

    # Кодируем имя файла
    import urllib.parse
    encoded_filename = urllib.parse.quote(file.original_name)

    return FileResponse(
        path=file_path,
        filename=file.original_name,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )


# Поддержка ФЦМПО
# Папка для хранения данных
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

REQUESTS_FILE = DATA_DIR / "fcmp_requests.json"


def load_requests():
    """Загрузка заявок из JSON файла"""
    try:
        if REQUESTS_FILE.exists():
            with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                if content:
                    return json.loads(content)
    except Exception as e:
        print(f"Ошибка загрузки заявок: {e}")
    return []


def save_requests(requests):
    """Сохранение заявок в JSON файл"""
    try:
        with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
            json.dump(requests, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Ошибка сохранения заявок: {e}")
        return False


# API эндпоинты
@app.post("/api/fcmp-request")
async def create_fcmp_request(request: Request):
    """Создание новой заявки"""
    try:
        data = await request.json()

        requests = load_requests()

        new_request = {
            "id": int(time.time() * 1000),
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "region": data.get("region", ""),
            "school": data.get("school", ""),
            "email": data.get("email", ""),
            "problem": data.get("problem", ""),
            "status": "pending",
            "reply": None,
            "reply_date": None
        }

        requests.append(new_request)
        save_requests(requests)

        return {"status": "success", "id": new_request["id"]}
    except Exception as e:
        print(f"Ошибка создания заявки: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/fcmp-admin-login")
async def fcmp_admin_login(request: Request):
    """Вход в админ-панель"""
    try:
        data = await request.json()
        code = data.get("code", "")

        if code == "alu3377%":
            request.session["fcmp_admin"] = True
            return {"status": "success"}
        return {"status": "error", "message": "Неверный код"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fcmp-complete-request")
async def complete_fcmp_request(request: Request):
    """Отметить заявку как выполненную"""
    if not request.session.get("fcmp_admin"):
        return {"status": "error", "message": "Не авторизован"}

    try:
        data = await request.json()
        request_id = data.get("id")

        requests = load_requests()
        found = False
        for req in requests:
            if req["id"] == request_id:
                req["status"] = "completed"
                found = True
                break

        if found:
            save_requests(requests)
            return {"status": "success"}
        return {"status": "error", "message": "Заявка не найдена"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fcmp-reply-request")
async def reply_fcmp_request(request: Request):
    """Ответить на заявку"""
    if not request.session.get("fcmp_admin"):
        return {"status": "error", "message": "Не авторизован"}

    try:
        data = await request.json()
        request_id = data.get("id")
        reply = data.get("reply", "")

        requests = load_requests()
        found = False
        for req in requests:
            if req["id"] == request_id:
                req["reply"] = reply
                req["reply_date"] = datetime.now().strftime("%d.%m.%Y %H:%M")
                found = True
                break

        if found:
            save_requests(requests)
            return {"status": "success"}
        return {"status": "error", "message": "Заявка не найдена"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fcmp-delete-request")
async def delete_fcmp_request(request: Request):
    """Удалить заявку"""
    if not request.session.get("fcmp_admin"):
        return {"status": "error", "message": "Не авторизован"}

    try:
        data = await request.json()
        request_id = data.get("id")

        requests = load_requests()
        new_requests = [req for req in requests if req["id"] != request_id]

        if len(new_requests) != len(requests):
            save_requests(new_requests)
            return {"status": "success"}
        return {"status": "error", "message": "Заявка не найдена"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Страница ФЦМПО
@app.get("/fcmp-support", response_class=HTMLResponse)
async def fcmp_support_page(request: Request):
    """Страница базы данных ФЦМПО"""
    requests = load_requests()
    is_admin = request.session.get("fcmp_admin", False)

    return templates.TemplateResponse("fcmp_support.html", {
        "request": request,
        "requests": requests,
        "is_admin": is_admin,
        "session": request.session
    })


# ========== ВИДЕОИНСТРУКЦИИ ==========
VIDEOS_FILE = DATA_DIR / "videos.json"


def load_videos():
    """Загрузка видео из JSON файла"""
    try:
        if VIDEOS_FILE.exists():
            with open(VIDEOS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                if content:
                    return json.loads(content)
    except Exception as e:
        print(f"Ошибка загрузки видео: {e}")
    return []


def save_videos(videos):
    """Сохранение видео в JSON файл"""
    try:
        with open(VIDEOS_FILE, "w", encoding="utf-8") as f:
            json.dump(videos, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Ошибка сохранения видео: {e}")
        return False


# API для получения видео
@app.get("/api/videos")
async def get_videos():
    """Получение списка видео"""
    videos = load_videos()
    return {"videos": videos}


# API для сохранения видео
@app.post("/api/videos")
async def save_videos_api(request: Request):
    """Сохранение списка видео"""
    try:
        data = await request.json()
        videos = data.get("videos", [])
        save_videos(videos)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        workers=4,
        loop="asyncio"
    )