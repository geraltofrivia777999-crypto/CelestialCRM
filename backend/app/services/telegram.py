"""Отправка уведомлений в Telegram — ТЗ 9.

Только два вызова Bot API: `getMe` для проверки токена и `sendMessage` для
доставки. Ошибку не глотаем и не превращаем в исключение выше по стеку: она
возвращается вызывающему коду и попадает в журнал событий. Иначе на вопрос
«почему алерт не пришёл» нечем ответить — в чате-то пусто.
"""

import httpx

API_ROOT = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_MESSAGE = 4000


class TelegramError(RuntimeError):
    pass


def _url(token: str, method: str) -> str:
    return f"{API_ROOT}/bot{token}/{method}"


def _explain(response: httpx.Response) -> str:
    """Человеческий текст ошибки вместо голого кода."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    description = str(payload.get("description") or "").strip()
    if response.status_code == 401:
        return "Telegram не принял токен бота — проверьте его в настройках"
    if response.status_code == 400 and "chat not found" in description.lower():
        return (
            "Telegram не нашёл чат. Добавьте бота в чат и пришлите туда любое "
            "сообщение, затем проверьте chat_id"
        )
    if response.status_code == 403:
        return "Бот исключён из чата или лишён права писать в него"
    if response.status_code == 429:
        return "Telegram ограничил частоту отправки — попробуйте позже"
    return description or f"Telegram ответил {response.status_code}"


async def check_token(token: str) -> dict:
    """Проверить токен и узнать имя бота."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(_url(token, "getMe"))
    except httpx.HTTPError as exc:
        raise TelegramError(f"Не удалось связаться с Telegram: {exc}") from exc
    if response.status_code != 200:
        raise TelegramError(_explain(response))
    result = response.json().get("result") or {}
    return {"username": result.get("username"), "id": result.get("id")}


async def list_chats(token: str) -> list[dict]:
    """Чаты, в которых бота видели за последние сутки.

    Единственный способ узнать chat_id, не заставляя человека искать его
    руками: Bot API не отдаёт список чатов бота, зато отдаёт последние
    события, включая «бота добавили в группу».

    Telegram хранит эти события 24 часа и отдаёт их только когда не настроен
    вебхук. Поэтому пустой список — не ошибка: он значит «свежих событий нет»,
    и chat_id остаётся ввести вручную.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                _url(token, "getUpdates"),
                params={"limit": 100, "timeout": 0},
            )
    except httpx.HTTPError as exc:
        raise TelegramError(f"Не удалось связаться с Telegram: {exc}") from exc
    if response.status_code != 200:
        raise TelegramError(_explain(response))

    found: dict[str, dict] = {}
    for update in response.json().get("result") or []:
        for payload in update.values():
            if not isinstance(payload, dict):
                continue
            chat = payload.get("chat")
            if not isinstance(chat, dict) or chat.get("id") is None:
                continue
            chat_id = str(chat["id"])
            title = (
                chat.get("title")
                or " ".join(
                    part
                    for part in (chat.get("first_name"), chat.get("last_name"))
                    if part
                )
                or chat.get("username")
                or chat_id
            )
            found[chat_id] = {
                "chat_id": chat_id,
                "title": title,
                "type": chat.get("type") or "chat",
            }
    return sorted(found.values(), key=lambda item: item["title"])


async def send_message(
    token: str, chat_id: str, text: str, thread_id: str | None = None
) -> None:
    """Отправить сообщение. Бросает TelegramError с понятным текстом.

    Разметка HTML включена: команда пишет в шаблонах `<b>`, `<code>` и
    `<blockquote>`, и без `parse_mode` они приезжали в чат текстом. Значения
    макросов экранируются при подстановке — угловая скобка в названии оффера
    остаётся текстом и разбор не ломает.

    Если Telegram всё же не разобрал разметку (в шаблоне забыли закрыть тег),
    сообщение уходит вторым заходом без неё: уведомление важнее оформления, и
    молчать из-за кривого тега нельзя.
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:MAX_MESSAGE],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    response = await _post(token, payload)
    if response.status_code == 400 and _is_markup_error(response):
        payload.pop("parse_mode")
        response = await _post(token, payload)
    if response.status_code != 200:
        raise TelegramError(_explain(response))


async def _post(token: str, payload: dict) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.post(_url(token, "sendMessage"), json=payload)
    except httpx.HTTPError as exc:
        raise TelegramError(f"Не удалось связаться с Telegram: {exc}") from exc


def _is_markup_error(response: httpx.Response) -> bool:
    try:
        description = str(response.json().get("description") or "")
    except ValueError:
        return False
    return "parse entities" in description.lower()
