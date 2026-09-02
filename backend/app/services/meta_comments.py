"""Модерация комментариев под постами объявлений.

Чистка ручная: человек смотрит список, отмечает мусор и удаляет или скрывает
его. Автоматики здесь нет намеренно — под рекламой оседают и спам, и живые
вопросы клиентов, и отличить одно от другого по ключевым словам нельзя без
ложных срабатываний, которые стоят дороже пары минут ручной работы.

Три вещи, которые определяют устройство этого модуля.

**Комментарии кешируются.** Удалённый комментарий Meta не отдаёт больше
никогда. Если не сохранить текст до удаления, в CRM не останется следа, за что
именно человека вычистили, — а это первое, о чём спрашивают, когда клиент
жалуется, что его вопрос стёрли.

**Всё идёт заданием с паузами.** Комментарий Meta считает пользовательским
действием, а не рекламным вызовом: полсотни удалений подряд без пауз — самый
быстрый способ получить чекпоинт на аккаунте. Поэтому и чтение, и запись идут
последовательно, с задержкой между вызовами, с потолком на задание и с отменой,
которая срабатывает на следующем комментарии.

**Способ авторизации меняет транспорт, но не логику.** У `session`-подключения
Meta принимает запись только из живого браузерного контекста — тот же
`open_session_access`, что и у автоправил. У `system_user` и `app_token` это
обычный Graph, но их токен выпускается под рекламные права, и без
`pages_read_engagement` комментарии не читаются вовсе. Разница видна человеку до
первого действия: перед списком стоит проверка доступа, которая называет
недостающее право, а не отдаёт пустой список.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaComment,
    MetaCommentJob,
    MetaEntity,
    MetaFanPage,
    MetaOperation,
)
from app.services.meta import MetaClient, client_with_token, creative_post_id
from app.services.meta_session import MetaSessionError, get_session_manager, open_session_access

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., MetaClient]

JOB_KINDS = {"fetch", "hide", "unhide", "delete"}
ACTION_KINDS = {"hide", "unhide", "delete"}
ACTION_LABELS = {
    "fetch": "Загрузка комментариев",
    "hide": "Скрытие комментариев",
    "unhide": "Возврат комментариев",
    "delete": "Удаление комментариев",
}
COMMENT_STATUSES = {"visible": "Виден", "hidden": "Скрыт", "deleted": "Удалён"}
# Статус, в который переводит успешно применённое действие.
ACTION_RESULT = {"hide": "hidden", "unhide": "visible", "delete": "deleted"}
ACTIVE_JOB_STATUSES = ("queued", "running")

# Ссылка в комментарии под рекламой — почти всегда либо конкурент, либо развод.
# Ловим и голые домены: «пиши в тг мойканал.рф» ссылкой в смысле URL не является,
# но это ровно тот же мусор.
LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|wa\.me/|\b[\w-]{2,}\.(?:com|net|org|ru|ua|kz|by|info|biz|"
    r"site|online|shop|store|club|xyz|top|link|me|io|co)\b)",
    re.IGNORECASE,
)
# Телефон: семь и больше цифр подряд, допускающих пробелы, дефисы и скобки.
PHONE_PATTERN = re.compile(r"(?:\+?\d[\s\-()]?){7,}\d")


class CommentAccessError(RuntimeError):
    """Доступа к комментариям нет, и причина понятна человеку."""


def has_link(message: str) -> bool:
    return bool(LINK_PATTERN.search(message or ""))


def has_phone(message: str) -> bool:
    return bool(PHONE_PATTERN.search(message or ""))


def parse_meta_time(value: object) -> datetime | None:
    """Время Meta вида «2026-08-27T09:12:00+0000»."""
    text = str(value or "").strip()
    if not text:
        return None
    # Python до 3.11 не понимает смещение без двоеточия; приводим сами, чтобы не
    # зависеть от версии интерпретатора на хосте.
    if len(text) >= 5 and (text[-5] in "+-") and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def describe_access_error(error: Exception) -> str:
    """Почему Meta не отдала комментарии — словами, а не кодом.

    Самый частый случай не «сломалось», а «токен выпущен под рекламу»: у
    System User и app-токена по умолчанию нет прав на страницу, и кабинеты при
    этом синхронизируются нормально. Человеку надо сказать, что именно
    перевыпустить, иначе он будет искать поломку в CRM.
    """
    if isinstance(error, MetaSessionError):
        return str(error)
    code = getattr(error, "error_code", None)
    text = " ".join(str(error).split())
    if code == 190:
        return (
            "Токен Meta больше не действует. Обновите подключение — "
            "комментарии читаются тем же токеном, что и статистика."
        )
    if code in {10, 200, 299}:
        return (
            "У токена нет прав на страницу. Комментарии требуют "
            "pages_read_engagement (чтение) и pages_manage_engagement (скрытие и "
            "удаление). Перевыпустите токен System User с этими правами или "
            "используйте подключение с токеном сессии — у него права страницы "
            f"есть по умолчанию. Ответ Meta: {text}"
        )
    if code in {100, 803}:
        return (
            "Meta не нашла пост или не дала к нему доступ. Обычно это значит, что "
            f"страница объявления не привязана к этому подключению. Ответ Meta: {text}"
        )
    return text or "Meta не ответила на запрос комментариев"


async def managed_page_tokens(client: MetaClient) -> dict[str, str]:
    """Токены страниц, которыми владеет пользователь текущего токена клиента.

    Пустой словарь означает «страниц не видно»: у System User на /me/accounts
    обычно нет доступа — это штатно, комментарии тогда читаем базовым токеном.
    Ошибку глушим: страница подтянется при следующем заходе, а проверка
    доступа в интерфейсе объяснит словами, чего не хватает.
    """
    try:
        return await client.page_tokens()
    except Exception:  # noqa: BLE001 — отсутствие страниц не ломает загрузку
        return {}


async def pages_without_post(db: AsyncSession, account: MetaAdAccount) -> list[str]:
    """Страницы объявлений кабинета без своего поста (динамическая реклама)."""
    rows = await db.execute(
        select(MetaEntity.page_external_id)
        .where(
            MetaEntity.account_id == account.id,
            MetaEntity.level == "ad",
            MetaEntity.post_external_id.is_(None),
            MetaEntity.page_external_id.is_not(None),
        )
        .distinct()
    )
    return [page for page in rows.scalars() if page]


async def account_posts(db: AsyncSession, account: MetaAdAccount) -> list[dict]:
    """Посты кабинета, под которыми могут быть комментарии.

    Один пост нередко крутится несколькими объявлениями, поэтому единицей
    чистки выступает пост, а объявления показываются как то, что на него
    ссылается.
    """
    rows = list(
        (
            await db.execute(
                select(MetaEntity)
                .where(
                    MetaEntity.account_id == account.id,
                    MetaEntity.level == "ad",
                    MetaEntity.post_external_id.is_not(None),
                )
                .order_by(MetaEntity.name)
            )
        ).scalars()
    )
    counts = dict(
        (
            await db.execute(
                select(MetaComment.post_external_id, func.count(MetaComment.id))
                .where(
                    MetaComment.account_id == account.id,
                    MetaComment.status != "deleted",
                )
                .group_by(MetaComment.post_external_id)
            )
        ).all()
    )
    fetched = dict(
        (
            await db.execute(
                select(MetaComment.post_external_id, func.max(MetaComment.fetched_at))
                .where(MetaComment.account_id == account.id)
                .group_by(MetaComment.post_external_id)
            )
        ).all()
    )
    posts: dict[str, dict] = {}
    for entity in rows:
        post_id = entity.post_external_id
        item = posts.get(post_id)
        if not item:
            item = {
                "post_external_id": post_id,
                "page_external_id": entity.page_external_id,
                "ads": [],
                "active_ads": 0,
                "comments": int(counts.get(post_id, 0)),
                "fetched_at": fetched.get(post_id),
            }
            posts[post_id] = item
        active = str(entity.effective_status or "").upper() == "ACTIVE"
        item["ads"].append(
            {
                "external_id": entity.external_id,
                "name": entity.name,
                "status": entity.effective_status,
                "is_active": active,
            }
        )
        item["active_ads"] += int(active)
    # Активные посты первыми: чистят почти всегда то, что крутится сейчас.
    return sorted(
        posts.values(),
        key=lambda item: (-item["active_ads"], -item["comments"], item["post_external_id"]),
    )


# Ссылка на пост, которую человек копирует из Facebook, бывает трёх видов, и
# только один из них Graph понимает напрямую.
POST_REF_RE = re.compile(r"(\d{5,})_(\d{5,})")
SHARE_REF_RE = re.compile(r"facebook\.com/share/", re.IGNORECASE)
DIGITS_RE = re.compile(r"^\d{5,}$")


def parse_post_reference(raw: object) -> dict:
    """Что человек вставил: пост, объявление или короткая ссылка.

    `facebook.com/share/p/XXXX` — короткая ссылка-редирект: в ней нет ни id
    страницы, ни id поста, и Graph по ней ничего не отдаст. Для подключения с
    сессией её раскрывает браузер в том же контексте и через тот же прокси.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return {"kind": "empty"}
    pair = POST_REF_RE.search(text)
    if pair:
        return {"kind": "post", "post_id": f"{pair.group(1)}_{pair.group(2)}"}
    if SHARE_REF_RE.search(text):
        return {"kind": "share"}
    bare = text.rsplit("/", 1)[-1].split("?", 1)[0]
    if DIGITS_RE.match(bare):
        # Голое число — это либо id объявления, либо id поста без страницы.
        # Различаем запросом к Graph: у объявления есть креатив.
        return {"kind": "number", "id": bare}
    return {"kind": "unknown"}


def share_post_id(urls: list[object]) -> str | None:
    """Достать Graph id поста из URL, увиденных после раскрытия share-ссылки.

    У непубличных постов новой страницы адрес остаётся вида
    ``permalink.php?story_fbid=pfbid...&id=<profile>``: pfbid нельзя передать в
    Graph как ``<page>_<post>``. Но Facebook помещает рядом ссылку управления
    продвижением с точными ``page_id`` и ``target_id`` — это и есть стабильный
    Graph id, под которым доступны комментарии.
    """
    for value in urls:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            query = parse_qs(urlparse(text).query)
        except ValueError:
            query = {}
        page_id = str((query.get("page_id") or [""])[0])
        target_id = str((query.get("target_id") or [""])[0])
        if DIGITS_RE.fullmatch(page_id) and DIGITS_RE.fullmatch(target_id):
            return f"{page_id}_{target_id}"
        pair = POST_REF_RE.search(text)
        if pair:
            return f"{pair.group(1)}_{pair.group(2)}"
    return None


async def resolve_share_post(connection_id: str, ref: str) -> dict:
    """Раскрыть ``facebook.com/share/...`` живой сессией подключения.

    Контекст уже должен быть восстановлен через :func:`open_session_access`.
    Поэтому новая вкладка наследует proxy, user-agent и cookies подключения;
    отдельного сетевого пути к Facebook здесь нет.
    """
    if not SHARE_REF_RE.search(str(ref or "")):
        return {"ok": False, "error": "Это не короткая ссылка Facebook share"}
    state = get_session_manager().get(str(connection_id))
    if not state or not state.context:
        return {
            "ok": False,
            "error": "Браузерная сессия подключения не восстановлена",
        }
    page = await state.context.new_page()
    try:
        await page.goto(str(ref), wait_until="domcontentloaded", timeout=60_000)
        # Share раскрывается клиентским кодом Facebook. Небольшая пауза нужна,
        # чтобы в DOM появился target рекламного поста, но кликов мы не делаем.
        await page.wait_for_timeout(5_000)
        payload = await page.evaluate(
            """() => ({
                current: window.location.href,
                canonical: document.querySelector('link[rel="canonical"]')?.href || '',
                ogUrl: document.querySelector('meta[property="og:url"]')?.content || '',
                links: Array.from(document.querySelectorAll('a[href]'))
                    .map((node) => node.href)
                    .filter((href) => href.includes('page_id=') ||
                        href.includes('target_id=') || href.includes('permalink.php'))
                    .slice(0, 100)
            })"""
        )
    except Exception as exc:  # noqa: BLE001 — причина показывается человеку
        return {"ok": False, "error": f"Не удалось раскрыть ссылку в Facebook: {exc}"}
    finally:
        await page.close()
    urls = [
        payload.get("current"),
        payload.get("canonical"),
        payload.get("ogUrl"),
        *(payload.get("links") or []),
    ]
    post_id = share_post_id(urls)
    if not post_id:
        return {
            "ok": False,
            "error": (
                "Facebook открыл ссылку, но не показал технический ID поста. "
                "Проверьте, что пользователь подключения видит эту публикацию."
            ),
        }
    return {
        "ok": True,
        "post_id": post_id,
        "via": "короткая ссылка через браузерную сессию",
    }


async def resolve_reference(client: MetaClient, parsed: dict) -> dict:
    """Довести разобранную ссылку до id поста через Graph."""
    if parsed["kind"] == "post":
        return {"ok": True, "post_id": parsed["post_id"], "via": "ссылка вида page_post"}
    if parsed["kind"] != "number":
        return {"ok": False}
    value = parsed["id"]
    # Сначала пробуем как объявление: у Dolphin и подобных ссылка чаще всего
    # ведёт на объявление, а пост у него — «тёмный», отдельным id.
    try:
        payload = await client.object_fields(
            value, ["creative{effective_object_story_id,object_story_spec}"]
        )
    except Exception:  # noqa: BLE001 — не объявление, попробуем как пост
        payload = {}
    story = creative_post_id(payload)
    if story:
        return {"ok": True, "post_id": story, "via": "объявление"}
    try:
        await client.object_fields(value, ["id"])
    except Exception as exc:  # noqa: BLE001 — текст Meta уходит человеку
        return {"ok": False, "error": describe_access_error(exc)}
    return {"ok": True, "post_id": value, "via": "id объекта"}


async def page_owners(
    db: AsyncSession, workspace_id: uuid.UUID
) -> dict[str, list[uuid.UUID]]:
    """Какие подключения знают каждую страницу.

    Страницы синхронизируются по подключениям, и на одном кабинете их бывает
    несколько. Комментарии тёмного поста Meta отдаёт только токеном страницы,
    а токен страницы есть лишь у того подключения, чей пользователь состоит в
    её ролях, — поэтому важно знать, у кого именно спрашивать.
    """
    rows = await db.execute(
        select(MetaFanPage.external_id, MetaFanPage.connection_id).where(
            MetaFanPage.workspace_id == workspace_id
        )
    )
    owners: dict[str, list[uuid.UUID]] = {}
    for external_id, connection_id in rows:
        if not external_id:
            continue
        owners.setdefault(str(external_id), []).append(connection_id)
    return owners


async def account_pages(db: AsyncSession, account: MetaAdAccount) -> list[dict]:
    """Страницы, от лица которых крутятся объявления кабинета.

    Кабинет нередко ведёт несколько страниц, и доступ к комментариям у них
    разный: проверять его по одному случайно выбранному посту — значит судить
    обо всём кабинете по чужой странице.
    """
    rows = await db.execute(
        select(MetaEntity.page_external_id, func.count(MetaEntity.id))
        .where(
            MetaEntity.account_id == account.id,
            MetaEntity.level == "ad",
            MetaEntity.page_external_id.is_not(None),
        )
        .group_by(MetaEntity.page_external_id)
    )
    pages = [
        {"page_external_id": str(page_id), "ads": int(count)}
        for page_id, count in rows
        if page_id
    ]
    names = dict(
        (
            await db.execute(
                select(MetaFanPage.external_id, MetaFanPage.name).where(
                    MetaFanPage.workspace_id == account.workspace_id
                )
            )
        ).all()
    )
    posts = dict(
        (
            await db.execute(
                select(MetaEntity.page_external_id, func.count(MetaEntity.id))
                .where(
                    MetaEntity.account_id == account.id,
                    MetaEntity.level == "ad",
                    MetaEntity.post_external_id.is_not(None),
                )
                .group_by(MetaEntity.page_external_id)
            )
        ).all()
    )
    for page in pages:
        page["name"] = names.get(page["page_external_id"]) or ""
        page["posts"] = int(posts.get(page["page_external_id"], 0))
    pages.sort(key=lambda item: (-item["posts"], -item["ads"], item["page_external_id"]))
    return pages


async def upsert_comments(
    db: AsyncSession,
    *,
    account: MetaAdAccount,
    connection_id: uuid.UUID,
    post_external_id: str,
    page_external_id: str | None,
    rows: list[dict],
) -> dict:
    """Разложить ответ Graph по таблице.

    Комментарии, которые раньше видели, а сейчас Meta не отдала, помечаются
    удалёнными: их убрал автор, другой модератор или сама Meta. Строку не
    выбрасываем — в ней остался текст, и по нему видно, что было.
    """
    existing = {
        row.external_id: row
        for row in (
            await db.execute(
                select(MetaComment).where(
                    MetaComment.account_id == account.id,
                    MetaComment.post_external_id == post_external_id,
                )
            )
        ).scalars()
    }
    now = datetime.now(UTC)
    seen: set[str] = set()
    added = 0
    for row in rows:
        external_id = str(row.get("id") or "").strip()
        if not external_id:
            continue
        seen.add(external_id)
        author = row.get("from") if isinstance(row.get("from"), dict) else {}
        parent = row.get("parent") if isinstance(row.get("parent"), dict) else {}
        message = str(row.get("message") or "")
        comment = existing.get(external_id)
        if not comment:
            comment = MetaComment(
                workspace_id=account.workspace_id,
                connection_id=connection_id,
                account_id=account.id,
                external_id=external_id,
                post_external_id=post_external_id,
            )
            db.add(comment)
            added += 1
        comment.page_external_id = page_external_id
        comment.parent_external_id = str(parent.get("id") or "") or None
        comment.author_external_id = str(author.get("id") or "") or None
        comment.author_name = str(author.get("name") or "")[:300] or None
        comment.message = message
        comment.like_count = int(row.get("like_count") or 0)
        comment.reply_count = int(row.get("comment_count") or 0)
        comment.has_link = has_link(message)
        comment.has_phone = has_phone(message)
        # Локальный статус ведём от факта: скрытие могли снять и в самом
        # Facebook, и наш «hidden» тогда врал бы.
        comment.status = "hidden" if bool(row.get("is_hidden")) else "visible"
        comment.created_time = parse_meta_time(row.get("created_time"))
        comment.fetched_at = now
    vanished = 0
    for external_id, comment in existing.items():
        if external_id in seen or comment.status == "deleted":
            continue
        comment.status = "deleted"
        comment.fetched_at = now
        vanished += 1
    return {"received": len(seen), "added": added, "vanished": vanished}


class CommentJobEngine:
    """Исполнитель заданий: загрузка комментариев и применение действий.

    Живёт в воркере. Один проход — одно задание; внутри строго последовательно,
    с паузой между вызовами и проверкой отмены перед каждым следующим.
    """

    def __init__(self, session_factory, client_factory: ClientFactory = MetaClient) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.delay = max(settings.meta_comments_delay_ms, 0) / 1000

    async def run(self, job_id: str | uuid.UUID) -> dict:
        job_uuid = uuid.UUID(str(job_id))
        context = await self._start(job_uuid)
        if not context:
            return {"status": "skipped", "processed": 0}
        session_access: dict | None = None
        try:
            session_access = await self._session_access(context)
            client = self.client_factory(
                (session_access or {}).get("token") or context["access_token"],
                proxy=context["proxy_url"],
                user_agent=context["user_agent"],
                transport=(session_access or {}).get("transport"),
            )
            if context["kind"] == "fetch":
                result = await self._run_fetch(job_uuid, context, client)
            else:
                result = await self._run_action(job_uuid, context, client)
        except Exception as exc:  # noqa: BLE001 — текст ошибки уходит в задание
            await self._finish(job_uuid, "failed", describe_access_error(exc))
            return {"status": "failed", "error": str(exc)}
        finally:
            if session_access and session_access.get("owned"):
                # Браузер, поднятый ради задания, закрываем: он держит память и
                # прокси-соединение, а следующее задание поднимет свой.
                try:
                    await get_session_manager().close(str(context["connection_id"]))
                except Exception:  # noqa: BLE001 — очистка не маскирует результат
                    pass
        status = "cancelled" if result.get("cancelled") else "done"
        await self._finish(job_uuid, status, None)
        return {"status": status, **result}

    async def _session_access(self, context: dict) -> dict | None:
        """Живой браузерный контекст — только для session-подключений."""
        if context["auth_method"] != "session":
            return None
        return await open_session_access(
            self.session_factory,
            str(context["connection_id"]),
            proxy_url=context["proxy_url"],
            user_agent=context["user_agent"],
        )

    async def _start(self, job_id: uuid.UUID) -> dict | None:
        async with self.session_factory() as db:
            job = await db.get(MetaCommentJob, job_id)
            if not job or job.status not in {"queued", "running"}:
                return None
            account = await db.get(MetaAdAccount, job.account_id)
            connection = await db.get(IntegrationConnection, job.connection_id)
            if not account or not connection:
                job.status = "failed"
                job.error = "Кабинет или подключение больше не существуют"
                job.finished_at = datetime.now(UTC)
                await db.commit()
                return None
            job.status = "running"
            job.started_at = datetime.now(UTC)
            await db.commit()
            return {
                "kind": job.kind,
                "scope": job.scope or {},
                "workspace_id": job.workspace_id,
                "account_id": account.id,
                "account_external_id": account.external_id,
                "connection_id": connection.id,
                "auth_method": connection.auth_method,
                "proxy_url": connection.proxy_url,
                "user_agent": connection.user_agent,
                "access_token": decrypt_secret(connection.api_key_encrypted),
                "created_by_id": job.created_by_id,
            }

    async def _run_fetch(
        self, job_id: uuid.UUID, context: dict, client: MetaClient
    ) -> dict:
        posts = [str(item) for item in (context["scope"].get("posts") or []) if str(item)]
        posts = posts[: max(settings.meta_comments_max_posts, 1)]
        pages = max(settings.meta_comments_pages_per_post, 1)
        received = 0
        failed = 0
        # Комментарии тёмных постов отдаёт только токен страницы: юзер-токен
        # получает пустоту даже когда коммент виден в Facebook. Токены страниц
        # берём один раз на задание, клиенты страниц кешируем по page id.
        page_tokens = await managed_page_tokens(client)
        page_clients: dict[str, MetaClient] = {}
        for index, post_id in enumerate(posts):
            if await self._cancelled(job_id):
                return {"cancelled": True, "received": received}
            if index:
                await asyncio.sleep(self.delay)
            post_client = client
            page_id = post_id.split("_", 1)[0]
            page_token = page_tokens.get(page_id)
            if page_token:
                if page_id not in page_clients:
                    page_clients[page_id] = client_with_token(client, page_token)
                post_client = page_clients[page_id]
            elif page_tokens:
                # Страницы нет среди наших — Graph отдаст пустой список вместо
                # отказа, и задание отрапортовало бы «успешно, 0 комментариев».
                # Честнее назвать это промахом и объяснить причину.
                failed += 1
                await self._bump(job_id, processed=1, failed=1)
                await self._record_failure(
                    job_id, context, "comments_fetch", post_id,
                    RuntimeError(
                        f"Страница {page_id} не в ролях у пользователя этого "
                        "подключения — комментарии её рекламных постов Meta не "
                        "отдаёт. Возьмите кабинет того подключения, которому "
                        "принадлежит страница, или добавьте пользователя в её роли."
                    ),
                )
                continue
            try:
                rows = await post_client.post_comments(post_id, pages=pages)
            except Exception as exc:  # noqa: BLE001 — пост мог быть удалён
                failed += 1
                await self._bump(job_id, processed=1, failed=1)
                await self._record_failure(job_id, context, "comments_fetch", post_id, exc)
                continue
            async with self.session_factory() as db:
                account = await db.get(MetaAdAccount, context["account_id"])
                if not account:
                    break
                stats = await upsert_comments(
                    db,
                    account=account,
                    connection_id=context["connection_id"],
                    post_external_id=post_id,
                    page_external_id=post_id.split("_", 1)[0] or None,
                    rows=rows,
                )
                await db.commit()
            received += stats["received"]
            await self._bump(job_id, processed=1, succeeded=1)
        return {"received": received, "failed": failed}

    async def _run_action(self, job_id: uuid.UUID, context: dict, client: MetaClient) -> dict:
        ids = [str(item) for item in (context["scope"].get("comments") or []) if str(item)]
        ids = ids[: max(settings.meta_comments_max_per_job, 1)]
        kind = context["kind"]
        done = 0
        failed = 0
        # Тот же принцип, что и в загрузке: модерируем токеном страницы,
        # потому что чужой юзер-токен тёмные посты не видит. Только у
        # комментария префикс — ID поста (нередко вообще без страницы:
        # «122246890…_965938…»), поэтому страницу достаём из сохранённого
        # поста комментария, а не из его собственного ID.
        page_tokens = await managed_page_tokens(client)
        page_clients: dict[str, MetaClient] = {}
        comment_posts: dict[str, str] = {}
        async with self.session_factory() as db:
            rows = await db.execute(
                select(MetaComment.external_id, MetaComment.post_external_id).where(
                    MetaComment.workspace_id == context["workspace_id"],
                    MetaComment.external_id.in_(ids),
                )
            )
            for external_id, post_external_id in rows:
                comment_posts[external_id] = str(post_external_id or "")
        for index, external_id in enumerate(ids):
            if await self._cancelled(job_id):
                return {"cancelled": True, "succeeded": done, "failed": failed}
            if index:
                await asyncio.sleep(self.delay)
            action_client = client
            post_external_id = comment_posts.get(external_id, "")
            page_id = post_external_id.split("_", 1)[0]
            page_token = page_tokens.get(page_id) if page_id else None
            if page_token:
                if page_id not in page_clients:
                    page_clients[page_id] = client_with_token(client, page_token)
                action_client = page_clients[page_id]
            operation_id = await self._open_operation(job_id, context, kind, external_id)
            try:
                if kind == "delete":
                    response = await action_client.delete_comment(external_id)
                else:
                    response = await action_client.hide_comment(external_id, kind == "hide")
            except Exception as exc:  # noqa: BLE001 — ошибка одного не рвёт задание
                failed += 1
                await self._close_operation(
                    operation_id, "failed", {}, " ".join(str(exc).split())[:500]
                )
                await self._bump(job_id, processed=1, failed=1)
                continue
            await self._close_operation(
                operation_id, "success", response if isinstance(response, dict) else {}, None
            )
            await self._mark(context, external_id, ACTION_RESULT[kind])
            done += 1
            await self._bump(job_id, processed=1, succeeded=1)
        return {"succeeded": done, "failed": failed}

    async def _mark(self, context: dict, external_id: str, status: str) -> None:
        async with self.session_factory() as db:
            comment = await db.scalar(
                select(MetaComment).where(
                    MetaComment.workspace_id == context["workspace_id"],
                    MetaComment.external_id == external_id,
                )
            )
            if not comment:
                return
            comment.status = status
            comment.acted_at = datetime.now(UTC)
            comment.acted_by_id = context["created_by_id"]
            await db.commit()

    async def _cancelled(self, job_id: uuid.UUID) -> bool:
        async with self.session_factory() as db:
            return bool(
                await db.scalar(
                    select(MetaCommentJob.cancel_requested).where(MetaCommentJob.id == job_id)
                )
            )

    async def _bump(
        self, job_id: uuid.UUID, *, processed: int = 0, succeeded: int = 0, failed: int = 0
    ) -> None:
        async with self.session_factory() as db:
            job = await db.get(MetaCommentJob, job_id)
            if not job:
                return
            job.processed += processed
            job.succeeded += succeeded
            job.failed += failed
            await db.commit()

    async def _open_operation(
        self, job_id: uuid.UUID, context: dict, kind: str, target: str
    ) -> uuid.UUID:
        async with self.session_factory() as db:
            operation = MetaOperation(
                workspace_id=context["workspace_id"],
                comment_job_id=job_id,
                kind=f"comment_{kind}",
                target_external_id=target,
                status="pending",
                request={"comment": target, "action": kind},
                created_by_id=context["created_by_id"],
            )
            db.add(operation)
            await db.commit()
            return operation.id

    async def _record_failure(
        self, job_id: uuid.UUID, context: dict, kind: str, target: str, error: Exception
    ) -> None:
        async with self.session_factory() as db:
            db.add(
                MetaOperation(
                    workspace_id=context["workspace_id"],
                    comment_job_id=job_id,
                    kind=kind,
                    target_external_id=target,
                    status="failed",
                    request={"post": target},
                    error=describe_access_error(error)[:500],
                )
            )
            await db.commit()

    async def _close_operation(
        self, operation_id: uuid.UUID, status: str, response: dict, error: str | None
    ) -> None:
        async with self.session_factory() as db:
            operation = await db.get(MetaOperation, operation_id)
            if not operation:
                return
            operation.status = status
            operation.response = response
            operation.error = error
            await db.commit()

    async def _finish(self, job_id: uuid.UUID, status: str, error: str | None) -> None:
        async with self.session_factory() as db:
            job = await db.get(MetaCommentJob, job_id)
            if not job:
                return
            job.status = status
            job.error = error
            job.finished_at = datetime.now(UTC)
            await db.commit()
