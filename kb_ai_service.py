"""ИИ-помощник справочного центра.

Провайдеры (OpenAI-совместимые):
  - pollinations — бесплатно, без ключа, доступен из РФ (по умолчанию)
  - neuralgate  — бесплатные модели через РФ-шлюз (нужен бесплатный ключ)
  - zveno       — бесплатные маршруты, ключ без карты
  - openrouter  — legacy (часто режется Cloudflare с РФ-серверов)

Если удалённый API недоступен — локальный режим: выжимки и подбор документов.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache
from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env", override=False)

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 28_000
MAX_PREVIEW_CHARS = 1_200
MAX_CATALOG_CHARS = 70_000

_PROVIDERS = {
    "pollinations": {
        "base_url": "https://text.pollinations.ai/openai",
        "model": "openai-fast",
        "keyless": True,
        "supports_json_object": False,
    },
    "neuralgate": {
        "base_url": "https://api.neuralgate.ru/v1/chat/completions",
        "model": "deepseek/deepseek-v4-flash:free",
        "keyless": False,
        "supports_json_object": True,
    },
    "zveno": {
        "base_url": "https://api.zveno.ai/v1/chat/completions",
        "model": "cohere/north-mini-code:free",
        "keyless": False,
        "supports_json_object": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "openai/gpt-4o-mini",
        "keyless": False,
        "supports_json_object": True,
    },
}

_text_cache: TTLCache = TTLCache(maxsize=128, ttl=3600)


def _provider_name() -> str:
    raw = (os.getenv("KB_AI_PROVIDER") or "").strip().lower()
    if raw in _PROVIDERS:
        return raw
    # Совместимость со старым .env
    if (os.getenv("OPENROUTER_API_KEY") or "").strip() and not (
        os.getenv("KB_AI_API_KEY") or ""
    ).strip():
        return "openrouter"
    return "pollinations"


def _provider_cfg() -> Dict[str, Any]:
    return dict(_PROVIDERS[_provider_name()])


def _api_key() -> str:
    return (
        os.getenv("KB_AI_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("NEURALGATE_API_KEY")
        or os.getenv("ZVENO_API_KEY")
        or ""
    ).strip()


def _base_url() -> str:
    override = (os.getenv("KB_AI_BASE_URL") or os.getenv("OPENROUTER_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return _provider_cfg()["base_url"]


def _model() -> str:
    return (
        os.getenv("KB_AI_MODEL")
        or os.getenv("OPENROUTER_MODEL")
        or _provider_cfg()["model"]
    ).strip()


def _site_url() -> str:
    return (
        os.getenv("KB_AI_SITE_URL")
        or os.getenv("OPENROUTER_SITE_URL")
        or "https://monitoring95.ru"
    ).strip()


def _site_name() -> str:
    return (
        os.getenv("KB_AI_SITE_NAME")
        or os.getenv("OPENROUTER_SITE_NAME")
        or "FoodMonitoring"
    ).strip()


def _http_proxy() -> Optional[str]:
    return (
        os.getenv("KB_AI_HTTP_PROXY")
        or os.getenv("OPENROUTER_HTTP_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip() or None


def remote_enabled() -> bool:
    """Удалённый LLM доступен: keyless-провайдер или задан ключ."""
    cfg = _provider_cfg()
    if cfg.get("keyless"):
        return True
    return bool(_api_key())


def is_configured() -> bool:
    """Всегда True: есть удалённый LLM и/или локальный запасной режим."""
    return True


def using_local_only() -> bool:
    return not remote_enabled()


def extract_text_from_file(file_path: Path) -> str:
    """Извлекает текст из PDF / DOCX / TXT / таблиц."""
    if not file_path.exists() or not file_path.is_file():
        return ""

    ext = file_path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            parts: List[str] = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
            return "\n".join(parts).strip()

        if ext == ".docx":
            from docx import Document as DocxDocument

            doc = DocxDocument(str(file_path))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()

        if ext in {".txt", ".md", ".csv", ".log"}:
            for encoding in ("utf-8", "cp1251", "latin-1"):
                try:
                    return file_path.read_text(encoding=encoding).strip()
                except UnicodeDecodeError:
                    continue
            return ""

        if ext in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook

            wb = load_workbook(str(file_path), read_only=True, data_only=True)
            rows: List[str] = []
            for sheet in wb.worksheets[:3]:
                rows.append(f"[{sheet.title}]")
                for i, row in enumerate(sheet.iter_rows(values_only=True)):
                    if i > 200:
                        break
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        rows.append(" | ".join(cells))
            return "\n".join(rows).strip()

    except Exception as exc:
        logger.warning("Не удалось извлечь текст из %s: %s", file_path, exc)
        return ""

    return ""


def get_document_text(base_dir: Path, relative_path: str, doc_id: int) -> str:
    path = base_dir / relative_path
    try:
        mtime = path.stat().st_mtime if path.exists() else 0
    except OSError:
        mtime = 0
    cache_key = f"{doc_id}:{mtime}"
    if cache_key in _text_cache:
        return _text_cache[cache_key]

    text = extract_text_from_file(path)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    _text_cache[cache_key] = text
    return text


def _format_remote_error(status: int, body: str, provider: str) -> str:
    lower = (body or "").lower()
    hint = ""
    if status in {401, 403}:
        hint = " Проверьте KB_AI_API_KEY и доступность провайдера из РФ."
        if "cloudflare" in lower or "security policy" in lower:
            hint = (
                " Провайдер, похоже, блокирует РФ-IP. "
                "Переключите KB_AI_PROVIDER на neuralgate или pollinations."
            )
    elif status == 402:
        if "free_limit" in lower or "retry after" in lower:
            hint = " Бесплатный лимит провайдера исчерпан — использован локальный режим."
        else:
            hint = " Недостаточно средств / лимит бесплатного тарифа."
    elif status == 429:
        hint = " Слишком много запросов — подождите и повторите."

    snippet = re.sub(r"\s+", " ", (body or "").strip())[:220]
    if snippet:
        return f"{provider} вернул ошибку {status}: {snippet}.{hint}"
    return f"{provider} вернул ошибку {status}.{hint}"


async def _post_chat(payload: Dict[str, Any]) -> str:
    """POST OpenAI-compatible chat/completions. Returns assistant content."""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx не установлен") from exc

    provider = _provider_name()
    url = _base_url()
    # Некоторые шлюзы ждут .../v1, а chat — на .../v1/chat/completions
    if not url.endswith("/chat/completions") and not url.endswith("/openai"):
        url = url.rstrip("/") + "/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"FoodMonitoring/1.0 ({_site_name()})",
        "HTTP-Referer": _site_url(),
        "Referer": _site_url(),
        "X-Title": _site_name(),
    }
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    proxy = _http_proxy()
    client_kwargs: Dict[str, Any] = {"timeout": 90.0}
    if proxy:
        client_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = resp.text[:800]
            logger.error("KB AI %s error %s: %s", provider, resp.status_code, detail)
            raise RuntimeError(_format_remote_error(resp.status_code, detail, provider))
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return (content or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Некорректный ответ {provider}") from exc


async def llm_chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    response_format: Optional[Dict[str, str]] = None,
) -> str:
    if not remote_enabled():
        raise RuntimeError("Удалённый ИИ не настроен")

    payload: Dict[str, Any] = {
        "model": _model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    cfg = _provider_cfg()
    if response_format and cfg.get("supports_json_object"):
        payload["response_format"] = response_format

    return await _post_chat(payload)


# Обратная совместимость
async def openrouter_chat(*args: Any, **kwargs: Any) -> str:
    return await llm_chat(*args, **kwargs)


def _parse_json_payload(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"reply": raw, "documents": []}


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[^\wа-яёА-ЯЁ]+", (text or "").lower()) if len(t) > 2]


_STOP = {
    "это", "как", "для", "что", "или", "при", "без", "все", "они", "она", "его",
    "ещё", "еще", "также", "того", "этот", "эта", "есть", "быть", "если", "чтобы",
    "какой", "какие", "какая", "нужно", "можно", "документ", "документы", "файл",
}


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    out: List[str] = []
    for p in parts:
        s = re.sub(r"\s+", " ", p).strip(" •\t-–—")
        if 40 <= len(s) <= 420:
            out.append(s)
    return out


def _score_sentence(sentence: str, query_tokens: List[str], freq: Counter) -> float:
    tokens = _tokenize(sentence)
    if not tokens:
        return 0.0
    score = 0.0
    for t in tokens:
        score += 0.35 * freq.get(t, 0)
        if t in query_tokens:
            score += 3.0
    # Бонус за «нормативные» маркеры
    markers = (
        "требован", "норм", "санпин", "обязател", "запрещ", "рекоменд",
        "срок", "температур", "калор", "меню", "питани", "гигиен",
    )
    low = sentence.lower()
    score += sum(1.5 for m in markers if m in low)
    # Чуть выше оцениваем более информативные предложения
    score += min(len(tokens), 28) * 0.05
    return score


def local_summarize(text: str, *, meta_title: str = "", question: str = "") -> str:
    """Локальная выжимка / ответ по тексту без внешнего API."""
    if not text:
        return (
            f"Текст файла «{meta_title or 'документ'}» извлечь не удалось "
            "(скан, видео или защищённый формат). Откройте файл вручную."
        )

    q = (question or "").strip()
    query_tokens = [t for t in _tokenize(q) if t not in _STOP]
    sentences = _split_sentences(text[:MAX_DOC_CHARS])
    if not sentences:
        snippet = re.sub(r"\s+", " ", text[:700]).strip()
        return f"**Кратко по документу**\n\n{snippet}…"

    # Частоты слов для TF-подобной оценки
    all_tokens = []
    for s in sentences:
        all_tokens.extend(t for t in _tokenize(s) if t not in _STOP)
    freq = Counter(all_tokens)

    ranked: List[Tuple[float, int, str]] = []
    for idx, s in enumerate(sentences):
        ranked.append((_score_sentence(s, query_tokens, freq), idx, s))
    ranked.sort(key=lambda x: (-x[0], x[1]))

    if q and not any(x in q.lower() for x in ("выжимк", "кратко", "summar", "итог")):
        picks = [s for _, _, s in ranked[:5] if _score_sentence(s, query_tokens, freq) > 0]
        if not picks:
            picks = [s for _, _, s in ranked[:3]]
        lines = "\n".join(f"• {s}" for s in picks)
        return (
            f"**Ответ по документу «{meta_title or 'документ'}»**\n\n"
            f"{lines}\n\n"
            "_Локальный режим: ответ собран по наиболее релевантным фрагментам текста._"
        )

    # Выжимка: берём топ, восстанавливаем порядок появления
    top = sorted(ranked[:7], key=lambda x: x[1])
    lines = "\n".join(f"• {s}" for _, _, s in top)
    return (
        f"**Выжимка: {meta_title or 'документ'}**\n\n"
        f"{lines}\n\n"
        "**На что обратить внимание:** сверьте сроки, нормы и обязательные требования "
        "в полном тексте документа — выжимка выделяет ключевые формулировки."
    )


def build_catalog_entries(
    documents: List[Any],
    base_dir: Path,
    document_types: Dict[str, str],
    categories: Dict[int, str],
    query: str = "",
) -> List[Dict[str, Any]]:
    tokens = [t for t in _tokenize(query) if t not in _STOP]
    entries: List[Dict[str, Any]] = []
    total_chars = 0

    scored = []
    for doc in documents:
        hay = " ".join(
            filter(
                None,
                [
                    doc.title or "",
                    doc.description or "",
                    doc.tags or "",
                    document_types.get(doc.document_type, ""),
                    categories.get(doc.category_id or 0, ""),
                ],
            )
        ).lower()
        score = sum(1 for t in tokens if t in hay) if tokens else 0
        scored.append((score, doc))

    scored.sort(key=lambda x: (-x[0], -(x[1].downloads_count or 0), -(x[1].id or 0)))

    for score, doc in scored:
        include_preview = score > 0 or not tokens or len(entries) < 12
        preview = ""
        if include_preview and (doc.file_extension or "").lower() in {
            ".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".xlsm"
        }:
            text = get_document_text(base_dir, doc.file_path, doc.id)
            preview = text[:MAX_PREVIEW_CHARS]

        entry = {
            "id": doc.id,
            "title": doc.title,
            "description": (doc.description or "")[:500],
            "type": document_types.get(doc.document_type, doc.document_type),
            "category": categories.get(doc.category_id or 0, "Без категории"),
            "tags": doc.tags or "",
            "extension": doc.file_extension or "",
            "downloads": doc.downloads_count or 0,
            "preview": preview,
            "_score": score,
        }
        chunk = json.dumps({k: v for k, v in entry.items() if k != "_score"}, ensure_ascii=False)
        if total_chars + len(chunk) > MAX_CATALOG_CHARS and entries:
            break
        entries.append(entry)
        total_chars += len(chunk)

    return entries


def local_search_documents(
    *,
    user_message: str,
    documents: List[Any],
    base_dir: Path,
    document_types: Dict[str, str],
    categories: Dict[int, str],
) -> Dict[str, Any]:
    catalog = build_catalog_entries(
        documents, base_dir, document_types, categories, user_message
    )
    matched = [e for e in catalog if e.get("_score", 0) > 0][:5]
    if not matched:
        matched = catalog[:3]

    recommended = []
    for e in matched:
        reason_bits = []
        if e.get("_score"):
            reason_bits.append("совпадение по названию/тегам/описанию")
        if e.get("category"):
            reason_bits.append(f"категория: {e['category']}")
        recommended.append(
            {
                "id": e["id"],
                "title": e["title"],
                "type": e["type"],
                "url": f"/knowledge-base/document/{e['id']}",
                "cover_url": None,
                "reason": "; ".join(reason_bits) or "популярный документ справочника",
            }
        )

    # cover_url заполним в вызывающем коде при наличии объекта
    if matched and any(e.get("_score", 0) > 0 for e in matched):
        titles = ", ".join(f"«{e['title']}»" for e in matched[:3])
        reply = (
            f"По запросу «{user_message.strip()}» лучше всего подходят: {titles}.\n\n"
            "Откройте карточку документа — там можно сделать ИИ-выжимку."
        )
    else:
        reply = (
            f"Точного совпадения по «{user_message.strip()}» не нашёл. "
            "Ниже — наиболее скачиваемые документы; уточните тему "
            "(СанПиН, меню, нормы, технологические карты)."
        )

    return {"reply": reply, "documents": recommended, "mode": "local"}


async def search_documents_with_ai(
    *,
    user_message: str,
    documents: List[Any],
    base_dir: Path,
    document_types: Dict[str, str],
    categories: Dict[int, str],
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    id_map = {d.id: d for d in documents}

    def _attach_covers(result: Dict[str, Any]) -> Dict[str, Any]:
        for item in result.get("documents") or []:
            doc = id_map.get(item.get("id"))
            if doc and getattr(doc, "cover_image_path", None):
                item["cover_url"] = f"/knowledge-base/cover/{doc.id}"
            elif "cover_url" not in item:
                item["cover_url"] = None
        return result

    # Для keyless Pollinations поиск держим локальным: общий IP-сервера
    # быстро упирается в free-limit, а подбор по каталогу и так точный.
    force_local_search = (
        not remote_enabled()
        or _provider_name() == "pollinations"
        or (os.getenv("KB_AI_SEARCH_LOCAL") or "").strip() in {"1", "true", "yes"}
    )
    if force_local_search:
        return _attach_covers(
            local_search_documents(
                user_message=user_message,
                documents=documents,
                base_dir=base_dir,
                document_types=document_types,
                categories=categories,
            )
        )

    catalog = build_catalog_entries(
        documents, base_dir, document_types, categories, user_message
    )
    catalog_clean = [{k: v for k, v in e.items() if k != "_score"} for e in catalog]
    catalog_json = json.dumps(catalog_clean, ensure_ascii=False)

    system = (
        "Ты — ИИ-помощник справочного центра по организации питания в образовательных учреждениях. "
        "По запросу пользователя подбери подходящие документы из каталога. "
        "Отвечай только на русском. Не выдумывай документы вне каталога. "
        "Если подходящих нет — честно скажи и предложи уточнить запрос. "
        "Верни строго JSON объект вида:\n"
        '{"reply":"понятный ответ пользователю (можно с markdown)","documents":[{"id":1,"reason":"почему подходит"}]}\n'
        "В documents включай только реально существующие id из каталога, максимум 5."
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        for item in history[-6:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:2000]})

    messages.append(
        {
            "role": "user",
            "content": (
                f"Каталог документов:\n{catalog_json}\n\n"
                f"Запрос пользователя: {user_message.strip()}"
            ),
        }
    )

    try:
        raw = await llm_chat(
            messages,
            temperature=0.2,
            max_tokens=1600,
            response_format={"type": "json_object"},
        )
        parsed = _parse_json_payload(raw)
    except Exception as exc:
        logger.warning("KB AI search fallback to local: %s", exc)
        result = local_search_documents(
            user_message=user_message,
            documents=documents,
            base_dir=base_dir,
            document_types=document_types,
            categories=categories,
        )
        result["reply"] = (
            result["reply"]
            + "\n\n_Сейчас использован локальный подбор (внешний ИИ временно недоступен)._"
        )
        return _attach_covers(result)

    recommended = []
    for item in parsed.get("documents") or []:
        try:
            doc_id = int(item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        doc = id_map.get(doc_id)
        if not doc:
            continue
        recommended.append(
            {
                "id": doc.id,
                "title": doc.title,
                "type": document_types.get(doc.document_type, "Документ"),
                "url": f"/knowledge-base/document/{doc.id}",
                "cover_url": f"/knowledge-base/cover/{doc.id}" if doc.cover_image_path else None,
                "reason": (item.get("reason") or "").strip()[:300],
            }
        )

    reply = (parsed.get("reply") or "").strip() or (
        "Не удалось сформировать ответ. Попробуйте переформулировать запрос."
    )
    return {"reply": reply, "documents": recommended, "mode": "remote"}


async def ask_about_document(
    *,
    document: Any,
    base_dir: Path,
    document_types: Dict[str, str],
    question: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    text = get_document_text(base_dir, document.file_path, document.id)
    meta = (
        f"Название: {document.title}\n"
        f"Тип: {document_types.get(document.document_type, document.document_type)}\n"
        f"Описание: {document.description or '—'}\n"
        f"Теги: {document.tags or '—'}\n"
        f"Формат: {document.file_extension or '—'}"
    )

    q = (question or "").strip()
    if not q:
        q = (
            "Сделай краткую выжимку документа: основные положения, важные требования, "
            "сроки/нормы если есть, и на что обратить внимание практическому специалисту."
        )

    if not remote_enabled():
        reply = local_summarize(text, meta_title=document.title or "", question=q)
        return {
            "reply": reply,
            "has_text": bool(text),
            "chars_used": min(len(text), MAX_DOC_CHARS) if text else 0,
            "mode": "local",
        }

    if not text:
        text_block = (
            "Текст файла извлечь не удалось (возможно, это скан, видео или защищённый файл). "
            "Опирайся только на метаданные выше и честно скажи, если данных недостаточно."
        )
    else:
        text_block = text[:MAX_DOC_CHARS]

    system = (
        "Ты — ИИ-помощник справочного центра. Отвечай на русском, ясно и по делу. "
        "Опирайся только на предоставленный текст документа и метаданные. "
        "Если ответа в тексте нет — так и скажи. Не выдумывай факты. "
        "Форматируй ответ структурированно (короткие абзацы или маркированные списки)."
    )

    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        for item in history[-8:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:3000]})

    messages.append(
        {
            "role": "user",
            "content": (
                f"{meta}\n\n--- Текст документа ---\n{text_block}\n--- Конец ---\n\n"
                f"Вопрос пользователя: {q}"
            ),
        }
    )

    try:
        reply = await llm_chat(messages, temperature=0.25, max_tokens=2200)
        mode = "remote"
    except Exception as exc:
        logger.warning("KB AI document fallback to local: %s", exc)
        reply = local_summarize(text, meta_title=document.title or "", question=q)
        reply += "\n\n_Сейчас использован локальный режим (внешний ИИ временно недоступен)._"
        mode = "local"

    return {
        "reply": reply,
        "has_text": bool(text),
        "chars_used": min(len(text), MAX_DOC_CHARS) if text else 0,
        "mode": mode,
    }
