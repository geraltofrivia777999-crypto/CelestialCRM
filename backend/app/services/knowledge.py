"""Блоки статей и права на разделы базы знаний — ТЗ 8.2.

Статья хранится списком блоков, как в Notion. Сервер их нормализует, а не
доверяет тому, что прислал редактор: тип блока должен быть из списка, текст —
без HTML, вложения — только те, что реально загружены в этот воркспейс. Иначе
достаточно одного POST с `<script>` внутри абзаца, чтобы статья выполняла чужой
код у каждого, кто её откроет.
"""

import html
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeAccess, KnowledgeSection, User

# Типы блоков из ТЗ 8.2. Всё, чего здесь нет, отбрасывается при сохранении.
BLOCK_TYPES = {
    "heading_1",
    "heading_2",
    "heading_3",
    "paragraph",
    "bulleted_list",
    "numbered_list",
    "checklist",
    "table",
    "image",
    "video",
    "code",
    "quote",
    "divider",
    "file",
    "page_link",
}
TEXT_BLOCKS = {
    "heading_1", "heading_2", "heading_3", "paragraph", "quote", "code",
}
LIST_BLOCKS = {"bulleted_list", "numbered_list", "checklist"}
ATTACHMENT_BLOCKS = {"image", "video", "file"}
MAX_BLOCKS = 500
MAX_TEXT = 20000
# Разрешённая разметка внутри текста блока. Ссылку оставляем, но только http(s):
# javascript: в href — это тот же XSS, просто записанный иначе.
INLINE_TAGS = re.compile(
    r"</?(?:b|strong|i|em|u|s|code|br)>|<a href=\"https?://[^\"<>]{1,500}\">|</a>",
    re.IGNORECASE,
)


def normalize_blocks(blocks: object) -> list[dict]:
    """Привести присланные блоки к тому, что мы готовы хранить и отдавать."""
    if not isinstance(blocks, list):
        return []
    result: list[dict] = []
    for raw in blocks[:MAX_BLOCKS]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        if kind not in BLOCK_TYPES:
            continue
        block: dict = {"id": _block_id(raw.get("id")), "type": kind}
        if kind in TEXT_BLOCKS:
            block["text"] = _clean_text(raw.get("text"), rich=kind != "code")
            if kind == "code":
                block["language"] = _short(raw.get("language"), 24)
        elif kind in LIST_BLOCKS:
            block["items"] = _clean_items(raw.get("items"), checked=kind == "checklist")
        elif kind == "table":
            block["rows"] = _clean_table(raw.get("rows"))
        elif kind in ATTACHMENT_BLOCKS:
            block["attachment_id"] = _uuid_or_none(raw.get("attachment_id"))
            block["url"] = _clean_url(raw.get("url"))
            block["caption"] = _clean_text(raw.get("caption"), rich=False)[:300]
            block["name"] = _short(raw.get("name"), 200)
            if not block["attachment_id"] and not block["url"]:
                continue
        elif kind == "page_link":
            block["article_id"] = _uuid_or_none(raw.get("article_id"))
            block["title"] = _short(raw.get("title"), 300)
            if not block["article_id"]:
                continue
        result.append(block)
    return result


def blocks_to_text(blocks: list[dict]) -> str:
    """Плоская выжимка для поиска: по JSON искать нечем."""
    parts: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind in TEXT_BLOCKS:
            parts.append(_strip_tags(str(block.get("text") or "")))
        elif kind in LIST_BLOCKS:
            parts.extend(
                _strip_tags(str(item.get("text") or "")) for item in block.get("items", [])
            )
        elif kind == "table":
            for row in block.get("rows", []):
                parts.extend(_strip_tags(str(cell or "")) for cell in row)
        elif kind in ATTACHMENT_BLOCKS:
            parts.append(str(block.get("caption") or ""))
            parts.append(str(block.get("name") or ""))
        elif kind == "page_link":
            parts.append(str(block.get("title") or ""))
    return " ".join(part for part in parts if part)[:100000]


def used_attachment_ids(blocks: list[dict]) -> set[uuid.UUID]:
    ids = set()
    for block in blocks:
        value = block.get("attachment_id")
        if value:
            ids.add(uuid.UUID(str(value)))
    return ids


DEFAULT_ACCESS = {
    "can_view": True,
    "can_create": False,
    "can_edit": False,
    "can_delete": False,
    "can_manage": False,
}
FULL_ACCESS = {key: True for key in DEFAULT_ACCESS}


async def section_rights(
    db: AsyncSession, user: User, *, is_admin: bool
) -> dict[uuid.UUID, dict]:
    """Права пользователя на каждый раздел с наследованием сверху вниз.

    Правило, заданное на родителе, действует на вложенные разделы, пока у них
    нет своего. Раздел без единого правила во всём дереве открыт на просмотр —
    иначе только что созданный раздел был бы невидим даже автору.

    Именное правило сильнее ролевого: «этот раздел видит только Ирина»
    описывается одним правилом на человека, а не ролью под одного человека.
    """
    sections = list(
        (
            await db.execute(
                select(KnowledgeSection)
                .where(KnowledgeSection.workspace_id == user.workspace_id)
                .order_by(KnowledgeSection.position)
            )
        ).scalars()
    )
    if not sections:
        return {}
    if is_admin:
        return {section.id: dict(FULL_ACCESS) for section in sections}

    rules = {
        row.section_id: row
        for row in (
            await db.execute(
                select(KnowledgeAccess).where(
                    KnowledgeAccess.workspace_id == user.workspace_id,
                    KnowledgeAccess.role_id == user.role_id,
                    KnowledgeAccess.user_id.is_(None),
                )
            )
        ).scalars()
    }
    # Именные правила перекрывают ролевые в том же разделе.
    rules.update(
        {
            row.section_id: row
            for row in (
                await db.execute(
                    select(KnowledgeAccess).where(
                        KnowledgeAccess.workspace_id == user.workspace_id,
                        KnowledgeAccess.user_id == user.id,
                    )
                )
            ).scalars()
        }
    )
    guarded = {
        row.section_id
        for row in (
            await db.execute(
                select(KnowledgeAccess).where(
                    KnowledgeAccess.workspace_id == user.workspace_id
                )
            )
        ).scalars()
    }

    by_id = {section.id: section for section in sections}
    rights: dict[uuid.UUID, dict] = {}

    def resolve(section_id: uuid.UUID, seen: set[uuid.UUID]) -> dict:
        if section_id in rights:
            return rights[section_id]
        if section_id in seen:
            return dict(DEFAULT_ACCESS)
        seen.add(section_id)
        section = by_id.get(section_id)
        rule = rules.get(section_id)
        if rule is not None:
            value = {
                "can_view": rule.can_view,
                "can_create": rule.can_create,
                "can_edit": rule.can_edit,
                "can_delete": rule.can_delete,
                "can_manage": rule.can_manage,
            }
        elif section_id in guarded:
            # На разделе есть правила, но ни одно не про этого человека —
            # значит, доступ закрыт. Именно так работает «папка только для
            # Ирины»: одно именное правило закрывает раздел для всех остальных.
            value = {key: False for key in DEFAULT_ACCESS}
        elif section and section.parent_id:
            value = dict(resolve(section.parent_id, seen))
        else:
            value = dict(DEFAULT_ACCESS)
        rights[section_id] = value
        return value

    for section in sections:
        resolve(section.id, set())
    return rights


def _block_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:40] if text else uuid.uuid4().hex[:12]


def _clean_text(value: object, *, rich: bool) -> str:
    """Экранировать всё, кроме разрешённой разметки.

    Сначала снимаем экранирование: редактор присылает innerHTML, и уже
    экранированный текст при повторном сохранении превратился бы в `&amp;amp;`.
    Затем проходим по строке, оставляя нетронутыми только теги из allowlist, —
    всё между ними экранируется целиком.
    """
    text = html.unescape(str(value or ""))[:MAX_TEXT]
    if not rich:
        return re.sub(r"<[^>]*>", "", text).strip()

    # contenteditable добавляет div/p при нажатии Enter. Это не блоки статьи,
    # а служебная разметка браузера: превращаем её в переносы строк, чтобы
    # пользователь никогда не видел буквальные «<div>» после сохранения.
    text = re.sub(r"<div(?:\s[^<>]*)?>", "<br>", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<p(?:\s[^<>]*)?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "<br>", text, flags=re.IGNORECASE)
    parts: list[str] = []
    position = 0
    for match in INLINE_TAGS.finditer(text):
        parts.append(html.escape(text[position : match.start()], quote=True))
        parts.append(match.group(0))
        position = match.end()
    parts.append(html.escape(text[position:], quote=True))
    cleaned = "".join(parts)
    return re.sub(
        r"^(?:\s*<br>\s*)+|(?:\s*<br>\s*)+$", "", cleaned, flags=re.IGNORECASE
    )


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", "", html.unescape(value)).strip()


def _clean_items(value: object, *, checked: bool) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value[:200]:
        if isinstance(raw, dict):
            item = {"text": _clean_text(raw.get("text"), rich=True)}
            if checked:
                item["checked"] = bool(raw.get("checked"))
        else:
            item = {"text": _clean_text(raw, rich=True)}
            if checked:
                item["checked"] = False
        items.append(item)
    return items


def _clean_table(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    rows = []
    for raw_row in value[:100]:
        if not isinstance(raw_row, list):
            continue
        rows.append([_clean_text(cell, rich=True)[:2000] for cell in raw_row[:20]])
    return rows


def _clean_url(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not re.match(r"^https?://", text, re.IGNORECASE):
        return None
    return text[:2000]


def _short(value: object, limit: int) -> str:
    return _strip_tags(str(value or ""))[:limit]


def _uuid_or_none(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None
