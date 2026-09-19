"""Хранилище вложений базы знаний — ТЗ 8.2.

Файлы лежат на диске, а не в базе: картинки и видео в статьях весят мегабайты, и
каждая выборка статьи тянула бы их вместе с текстом. В базе остаются только путь
и метаданные.

Отдаются файлы всегда через API с проверкой прав на раздел. Раздавать их nginx-ом
напрямую нельзя: раздел может быть закрыт для роли, а прямая ссылка обошла бы
это ограничение — угадывать имя файла не потребовалось бы, оно есть в статье.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings

# Что разрешено вставлять в статью. Список закрытый: HTML и SVG сюда не входят
# намеренно — они исполняют скрипты в браузере того, кто откроет вложение.
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/zip",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}
ALLOWED_MIME_TYPES = IMAGE_MIME_TYPES | VIDEO_MIME_TYPES | DOCUMENT_MIME_TYPES

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_BLOCKED_TEXT_PREFIXES = (
    b"<!doctype html",
    b"<html",
    b"<script",
    b"<svg",
    b"<?xml",
)


class StorageError(ValueError):
    pass


def root() -> Path:
    return Path(settings.upload_dir)


def kind_for(mime_type: str) -> str:
    if mime_type in IMAGE_MIME_TYPES:
        return "image"
    if mime_type in VIDEO_MIME_TYPES:
        return "video"
    return "file"


def safe_file_name(name: str) -> str:
    """Имя для показа: без путей, без управляющих символов, ограниченной длины."""
    clean = Path(str(name or "")).name
    clean = unicodedata.normalize("NFKC", clean).strip()
    return clean[:200] or "file"


def _ascii_part(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _SAFE_NAME.sub("_", folded).strip("._")


def disposition(file_name: str, *, inline: bool) -> str:
    """Content-Disposition с настоящим именем файла.

    Раньше в заголовок уходил id вложения, и файл сохранялся на диск как
    «36d124b8-…» без расширения: имя из заголовка сильнее атрибута `download`
    у ссылки, поэтому браузер брал именно его.

    Имя приходит от пользователя, поэтому в кавычках едет только ASCII без
    кавычек и переводов строк — иначе заголовок можно было бы разорвать и
    подставить свой. Настоящее имя (кириллица, пробелы) уходит в `filename*`
    по RFC 5987, его понимают все живые браузеры.
    """
    clean = safe_file_name(file_name)
    # Имя и расширение чистим порознь: от кириллического имени в ASCII не
    # остаётся ничего, и без этого запасным вариантом стало бы одно расширение.
    stem = _ascii_part(Path(clean).stem) or "file"
    extension = _ascii_part(Path(clean).suffix.lstrip("."))
    ascii_name = stem + ("." + extension if extension else "")
    return (
        f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(clean, safe='')}"
    )


# Один и тот же .zip браузеры называют по-разному: Chrome на Windows шлёт
# `application/x-zip-compressed`, часть клиентов — `octet-stream`. Тип из
# multipart и без того не доказательство: настоящий формат подтверждает
# сигнатура, поэтому синонимы просто сводим к одному имени.
_ZIP_ALIASES = {
    "application/x-zip-compressed",
    "application/x-zip",
    "application/x-compressed",
    "multipart/x-zip",
    "application/octet-stream",
}


def normalize_mime_type(mime_type: str, content: bytes | None = None) -> str:
    """Normalize a multipart Content-Type before validation and persistence."""
    clean = str(mime_type or "").split(";", 1)[0].strip().lower()
    if clean in _ZIP_ALIASES and content is not None and content.startswith(_ZIP_SIGNATURES):
        return "application/zip"
    return clean


def validate_content(mime_type: str, content: bytes) -> None:
    """Verify that bytes match the declared type.

    ``UploadFile.content_type`` is supplied by the browser and is therefore not
    evidence of a file's real type.  We intentionally use a small, explicit
    signature allowlist instead of MIME guessing: guessing can turn unknown
    active content into something that is rendered inline by the browser.

    This is format identification, not antivirus scanning.  Images and video
    are still served with ``nosniff`` and documents are downloaded.
    """
    mime_type = normalize_mime_type(mime_type, content)
    if mime_type not in ALLOWED_MIME_TYPES:
        raise StorageError(
            "Такой тип файла загружать нельзя. Подойдут картинки, видео MP4/WEBM, "
            "PDF, документы Office, архивы ZIP, txt и csv."
        )

    valid = False
    if mime_type == "image/jpeg":
        valid = content.startswith(b"\xff\xd8\xff")
    elif mime_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif mime_type == "image/gif":
        valid = content.startswith((b"GIF87a", b"GIF89a"))
    elif mime_type == "image/webp":
        valid = (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    elif mime_type in {"video/mp4", "video/quicktime"}:
        # Both ISO BMFF/MP4 and QuickTime start with an ftyp box.  The brand is
        # deliberately not over-constrained: phones use a wide set of brands.
        valid = len(content) >= 12 and content[4:8] == b"ftyp"
    elif mime_type == "video/webm":
        valid = content.startswith(b"\x1aE\xdf\xa3")
    elif mime_type == "application/pdf":
        valid = content.startswith(b"%PDF-")
    elif mime_type in {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        valid = content.startswith(_ZIP_SIGNATURES)
    elif mime_type in {"application/msword", "application/vnd.ms-excel"}:
        valid = content.startswith(_OLE_SIGNATURE)
    elif mime_type in {"text/plain", "text/csv"}:
        valid = _is_safe_utf8_text(content)

    if not valid:
        raise StorageError(
            "Содержимое файла не соответствует заявленному типу или файл повреждён"
        )


def _is_safe_utf8_text(content: bytes) -> bool:
    """Accept actual UTF-8 text, but not active markup disguised as text."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    # Tabs/newlines are valid text; the other C0 controls usually indicate a
    # binary file with a forged text MIME type.
    if any(ord(character) < 32 and character not in "\t\n\r" for character in text):
        return False
    prefix = text.lstrip().lower().encode("utf-8")[:64]
    return not prefix.startswith(_BLOCKED_TEXT_PREFIXES)


def store(workspace_id: uuid.UUID, file_name: str, mime_type: str, content: bytes) -> str:
    """Записать файл и вернуть относительный путь для базы.

    Имя на диске генерируется целиком нами: пользовательское имя может быть
    каким угодно, включая `../`, и в путь оно не попадает вообще.
    """
    if not content:
        raise StorageError("Файл пустой")
    if len(content) > settings.upload_max_bytes:
        raise StorageError(
            f"Файл больше {settings.upload_max_bytes // (1024 * 1024)} МБ"
        )
    mime_type = normalize_mime_type(mime_type, content)
    validate_content(mime_type, content)

    suffix = _SAFE_NAME.sub("", Path(safe_file_name(file_name)).suffix)[:12]
    today = datetime.now(UTC).strftime("%Y/%m")
    relative = f"{workspace_id}/{today}/{uuid.uuid4().hex}{suffix}"
    target = root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise StorageError("Не удалось сохранить файл на диске") from exc
    return relative


def resolve(storage_path: str) -> Path:
    """Абсолютный путь к вложению с защитой от выхода за пределы хранилища."""
    base = root().resolve()
    target = (base / storage_path).resolve()
    if not target.is_relative_to(base):
        raise StorageError("Некорректный путь к файлу")
    if not target.is_file():
        raise StorageError("Файл не найден на диске")
    return target


def remove(storage_path: str) -> None:
    try:
        resolve(storage_path).unlink()
    except (StorageError, OSError):
        # Файла может уже не быть — запись в базе всё равно удаляется, иначе
        # статья навсегда останется с битой ссылкой на вложение.
        return
