"""Браузерные сессии Meta для подключений «Токен сессии (EAAB)».

Зачем это нужно

EAAB-токен живёт, пока жива сессия аккаунта в браузере. Если вставить готовую
строку, вытащенную в другом месте, и ходить ей с другого IP — Meta отзовёт и
токен, и сессию, а при регулярных повторах забанит сам аккаунт. Поэтому токен
здесь получают так, как это делает сам человек: открывается настоящий браузер
с cookies аккаунта и его прокси, сессия подтверждается (при капче/чекпойнте —
ручным входом через VNC), и только из этой живой сессии извлекается EAAB.
Все дальнейшие запросы CRM идут через тот же прокси, поэтому «токен сменил
IP» для Meta не происходит.

Правила безопасности (нарушение = бан):

  1. Без прокси браузер НЕ запускается вообще — cookies, показанные чужому IP,
     помечают сессию как угнанную.
  2. Прокси проверяется ДО запуска: если он не работает, браузер не стартует.
  3. Сессия сохраняется на диск (cookies + localStorage + fingerprint), чтобы
     при смерти токена синхронизация могла восстановить её и извлечь новый
     EAAB без участия человека.

Портировано из FBads (session_manager.py + token_extractor.py) и адаптировано
под настройки и модель подключений CelestialCRM.
"""

import asyncio
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.core.config import settings

logger = logging.getLogger("meta_session")

# Регулярка для извлечения EAAB-токена из DOM/JS-контекстов.
EAAB_TOKEN_RE = re.compile(r"EAAB\w{50,300}")

# JS-выражение для поиска EAAB в window.___fbConfig (используется несколько раз).
FBCONFIG_TOKEN_JS = r"""() => {
    try {
        const raw = JSON.stringify(window.___fbConfig || {});
        const m = raw.match(/EAAB\w{50,300}/);
        return m ? m[0] : null;
    } catch (e) { return null; }
}"""

# Facebook-cookies, которые обычно httpOnly/secure — выставляем флаги на основе имени.
_HTTPONLY_COOKIES = {"xs", "fr", "c_user", "datr", "sb", "presence", "wd", "locale", "i_user", "dpr"}
_SECURE_COOKIES = {"xs", "sb", "fr", "c_user", "datr", "presence", "wd", "locale", "i_user", "dpr"}

# Playwright принимает sameSite строго из (Strict|Lax|None), иначе падает:
#   "BrowserContext.add_cookies: cookies[0].sameSite: expected one of (Strict|Lax|None)"
# Экспорт из расширений отдаёт другие значения — нормализуем их.
_SAMESITE_NORMALIZE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",  # EditThisCookie / Chrome DevTools ("no_restriction" == None)
    "unspecified": "Lax",  # Firefox
    "": "Lax",
}

# Stealth-сниппеты, которые внедряются ДО загрузки любых скриптов страницы:
# init script выполняется раньше страничного JS, поэтому мы успеваем подменить
# свойства до проверок Facebook.
STEALTH_INIT_SCRIPT = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Chrome обычно отдаёт 5 плагинов. Рандомизируем набор, чтобы не быть "идеальными".
const plugins = [
  'PDF Viewer,Portable Document Format,application/x-google-chrome-pdf-pdf,PDF Viewer',
  'Chromium PDF Viewer,Portable Document Format,application/x-google-chrome-pdf-pdf,Chromium PDF Viewer',
  'Chromium PDF Viewer,Portable Document Format,application/pdf,Chromium PDF Viewer',
  'Microsoft Edge PDF Viewer,Portable Document Format,application/x-google-chrome-pdf-pdf,Microsoft Edge PDF Viewer',
  'WebKit built-in PDF,Portable Document Format,application/pdf,WebKit built-in PDF',
];
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [];
    for (const p of plugins) {
      const [name, desc, type, fname] = p.split(',');
      const mime = { type, suffixes: 'pdf', description: desc };
      const plugin = { name, description: desc, filename: fname, length: 1, item: i => mime, namedItem: n => mime };
      arr.push(plugin);
    }
    arr.item = i => arr[i];
    arr.namedItem = n => arr.find(x => x.name === n) || null;
    arr.refresh = () => {};
    return arr;
  }
});

Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// Подмена WebGL vendor — частый маркер автоматизации / виртуализации.
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel Iris Graphics';
  return getParameter.call(this, parameter);
};
const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
if (getParameter2) {
  WebGL2RenderingContext.prototype.getParameter = function (parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris Graphics';
    return getParameter2.call(this, parameter);
  };
}

// Маскируем хромодополнения и кадр времени (никакой суперстабильности таймеров).
Object.defineProperty(window, 'chrome', {
  get: () => ({
    runtime: { connect: () => {}, sendMessage: () => {} },
    csi: () => {},
    loadTimes: () => {},
    app: { isInstalled: false },
  })
});

const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);

// Небольшой "человеческий" шум: лёгкие таймер-джиттеры, чтобы временны́е паттерны
// не выглядели как машина.
if (!window._celestialPatched) {
  window._celestialPatched = true;
  const _t = window.setTimeout;
  window.setTimeout = function (fn, ms, ...args) {
    const jitter = Math.floor(Math.random() * 8);
    return _t.call(window, fn, ms + jitter, ...args);
  };
}
"""


class MetaSessionError(RuntimeError):
    """Сессию запустить нельзя или она умерла. Текст показывается пользователю."""


def _normalize_samesite(value) -> str:
    return _SAMESITE_NORMALIZE.get((value or "").strip().lower(), "Lax")


def _as_bool(value, default: bool = False) -> bool:
    """bool("false") == True — поэтому булевы поля нормализуем явно."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_expires(value):
    """Playwright принимает expires как float (unix-секунды) или -1."""
    if value is None:
        return -1.0
    try:
        ts = float(value)
        return ts if ts > 0 else -1.0
    except (TypeError, ValueError):
        return -1.0


def parse_cookies(text: str) -> list[dict]:
    """Парсит cookies из поля формы в формат Playwright add_cookies.

    Поддерживаются форматы:
      - строка header'а:   "name=value; name2=value2" (одной строкой или построчно)
      - строки "name=value" по одной на строку
      - JSON-массив (экспорт Playwright storage_state): [{"name":..,"value":..}, ...]
    Домен по умолчанию — .facebook.com (охватывает www и поддомены).
    """
    text = (text or "").strip()
    if not text:
        return []

    # 1) JSON-массив
    if text.lstrip().startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                cookies = []
                for c in arr:
                    if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                        same_site = _normalize_samesite(c.get("sameSite"))
                        secure = _as_bool(c.get("secure"), True)
                        if same_site == "None":
                            # SameSite=None требует Secure — иначе Chromium отклонит cookie
                            secure = True
                        cookies.append({
                            "name": c["name"],
                            "value": str(c["value"]),
                            "domain": c.get("domain") or ".facebook.com",
                            "path": c.get("path") or "/",
                            "secure": secure,
                            "httpOnly": _as_bool(c.get("httpOnly"), c["name"] in _HTTPONLY_COOKIES),
                            "sameSite": same_site,
                            "expires": _as_expires(c.get("expires", c.get("expirationDate"))),
                        })
                return cookies
        except json.JSONDecodeError:
            pass  # не JSON — парсим как текст

    # 2) Текст "name=value; name2=value2" (убираем префикс "Cookie:" если вставлен целиком)
    cleaned = re.sub(r"^\s*Cookie\s*:", "", text, flags=re.IGNORECASE)
    pairs = re.split(r"[;\n]", cleaned)
    cookies = []
    for pair in pairs:
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip().strip('"')
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".facebook.com",
            "path": "/",
            "secure": name in _SECURE_COOKIES,
            "httpOnly": name in _HTTPONLY_COOKIES,
            "sameSite": "Lax",
            "expires": -1,
        })
    return cookies


def parse_proxy_url(proxy_url: str) -> dict | None:
    """Разбирает прокси-URL формы scheme://user:pass@host:port в конфиг Playwright.

    Поддерживаются http, https и socks5 — ровно то, что принимает Chromium.
    Для socks5h (разрешение DNS на прокси) Playwright принимает socks5.
    Принимается и формат продавцов прокси host:port:user:pass (со схемой или
    без неё — тогда подразумевается socks5) — он нормализуется в
    user:pass@host:port.
    """
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        # Без схемы принимаем только формат продавцов прокси host:port:user:pass.
        parts = proxy_url.split(":")
        if len(parts) == 4 and parts[1].isdigit():
            host, port, username, password = parts
            proxy_url = f"socks5://{username}:{password}@{host}:{port}"
        else:
            raise MetaSessionError(
                "Прокси задаётся в виде socks5://логин:пароль@хост:порт "
                "(или socks5://хост:порт:логин:пароль) — без схемы браузер "
                "его не примет."
            )
    scheme, _, rest = proxy_url.partition("://")
    scheme = scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in {"http", "https", "socks5"}:
        raise MetaSessionError(
            f"Тип прокси «{scheme}» не поддерживается браузером. "
            "Используйте HTTP, HTTPS или SOCKS5."
        )
    # Формат продавцов прокси: socks5://host:port:user:pass (4 части, без @).
    if "@" not in rest and not rest.startswith("["):
        parts = rest.split(":")
        if len(parts) == 4 and parts[1].isdigit():
            host, port, username, password = parts
            rest = f"{username}:{password}@{host}:{port}"
    try:
        parsed = urlsplit(f"{scheme}://{rest}")
        port = parsed.port
    except ValueError as exc:
        raise MetaSessionError(f"Некорректный адрес прокси: {exc}") from exc
    if not parsed.hostname:
        raise MetaSessionError("Некорректный адрес прокси: не указан хост.")
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else 1080
    cfg = {"server": f"{scheme}://{parsed.hostname}:{port}"}
    if parsed.username:
        cfg["username"] = unquote(parsed.username)
    if parsed.password:
        cfg["password"] = unquote(parsed.password)
    return cfg


def normalize_proxy_url(proxy_url: str) -> str:
    """Возвращает прокси-URL в каноническом виде scheme://user:pass@host:port.

    Нужно httpx-клиенту (проверка прокси, MetaClient): он требует URL, а не
    отдельные поля user/pass. Кидает MetaSessionError при некорректном адресе.
    """
    cfg = parse_proxy_url(proxy_url)
    if not cfg:
        return ""
    scheme, _, hostport = cfg["server"].partition("://")
    if cfg.get("username") or cfg.get("password"):
        user = quote(cfg.get("username") or "", safe="")
        password = quote(cfg.get("password") or "", safe="")
        return f"{scheme}://{user}:{password}@{hostport}"
    return cfg["server"]


async def check_proxy_url(proxy_url: str) -> dict:
    """Проверка прокси перед запуском браузера: внешний IP, гео, задержка.

    Запрос к IP-чекеру идёт строго через прокси. Возвращает
    {ok, ip, country, latency_ms, error}. Это тот же маршрут, которым позже
    пойдёт браузер — если здесь не прошло, браузер запускать нельзя.
    """
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return {"ok": False, "error": "Прокси не задан"}
    try:
        proxy_url = normalize_proxy_url(proxy_url)
    except MetaSessionError as exc:
        return {"ok": False, "error": str(exc)}
    if not proxy_url:
        return {"ok": False, "error": "Прокси не задан"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0, verify=False) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            latency_ms = int((time.monotonic() - started) * 1000)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("proxy check failed for %s: %s", proxy_url.rsplit("@", 1)[-1], exc)
        return {
            "ok": False,
            "error": f"Не удалось подключиться через прокси: {exc}",
        }
    ip = str(data.get("ip") or "")
    return {"ok": True, "ip": ip, "country": None, "latency_ms": latency_ms}


class SessionHTTPTransport(httpx.AsyncBaseTransport):
    """httpx-транспорт, ходящий через APIRequestContext живой браузерной сессии.

    Meta принимает запросы с сессионным EAAB-токеном только из браузерного
    контекста (TLS-отпечаток браузера + cookies сессии + тот же IP). Обычный
    httpx-запрос с таким токеном получает «Invalid request» (код 1) или 2500 —
    а тот же запрос изнутри контекста сессии проходит. Проверено в бою:
    curl/httpx через прокси — 400, ctx.request — 200.
    """

    def __init__(self, context, timeout_ms: int = 45000) -> None:
        self.context = context
        self.timeout_ms = timeout_ms

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Хост-заголовок браузер выставит сам по URL — отдавать его снаружи нельзя.
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        # У multipart-запросов (загрузка креативов) тело потоковое: .content
        # упадёт с RequestNotRead — read() вычитывает стрим целиком. Стрим
        # одноразовый, а MetaClient повторяет запросы тем же объектом request —
        # кешируем тело, чтобы повторная попытка не ушла с пустым телом.
        body = getattr(request, "_celestial_body", None)
        if body is None:
            body = request.read() or None
            try:
                request._celestial_body = body
            except Exception:  # noqa: BLE001 — кеш не обязателен
                pass
        try:
            if request.method == "GET":
                response = await self.context.request.get(
                    str(request.url), headers=headers, timeout=self.timeout_ms
                )
            elif request.method == "POST" and body:
                # Graph API принимает form-urlencoded и multipart. httpx уже
                # сериализовал тело — отдаём его как есть с его content-type.
                response = await self.context.request.post(
                    str(request.url), headers=headers, data=body, timeout=self.timeout_ms
                )
            elif request.method == "POST":
                response = await self.context.request.post(
                    str(request.url), headers=headers, timeout=self.timeout_ms
                )
            else:
                response = await self.context.request.fetch(
                    str(request.url),
                    method=request.method,
                    headers=headers,
                    data=body,
                    timeout=self.timeout_ms,
                )
        except Exception as exc:
            # Внутренности MetaClient повторяют запросы по httpx-исключениям —
            # сетевую ошибку браузерного контекста представляем как ConnectError.
            raise httpx.ConnectError(f"session request failed: {exc}", request=request) from exc
        text = await response.text()
        # Playwright уже распаковал тело (gzip/br/deflate), поэтому заголовки
        # кодирования убираем — иначе httpx попытается распаковать ещё раз и
        # упадёт с DecodingError. content-length тоже устарел.
        resp_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        }
        return httpx.Response(
            status_code=response.status,
            headers=resp_headers,
            text=text,
            request=request,
        )


@dataclass
class MetaSessionState:
    """Состояние браузерной сессии в рантайме (не сериализуется напрямую)."""

    id: str
    context: object | None = None
    page: object | None = None
    status: str = "idle"  # idle | starting | waiting_login | saved | token | restoring | error
    error: str | None = None
    token: str | None = None
    methods_tried: list = field(default_factory=list)
    saved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    fingerprint: dict = field(default_factory=dict)
    browser: object | None = None
    pw: object | None = None
    bridge: object | None = None
    proxy_url: str = ""
    user_agent: str | None = None
    # Сериализует извлечение токена: параллельные вызовы /token и фоновая задача
    # не должны гоняться за одной страницей. Создаётся лениво в extract_token,
    # потому что asyncio.Lock обязан родиться внутри работающего event loop.
    extract_lock: object | None = None
    # Кто запустил сессию из мастера: только ему (и администратору) можно её
    # смотреть, закрывать и доставать из неё токен.
    owner_id: object | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "token_ready": bool(self.token),
            "methods_tried": list(self.methods_tried),
            "saved_at": self.saved_at.isoformat() if self.saved_at else None,
            "created_at": self.created_at.isoformat(),
            "fingerprint": self.fingerprint,
            "proxy_url": self.proxy_url,
        }


class MetaSessionManager:
    """Жизненный цикл Playwright-сессии: запуск, вход, извлечение EAAB, сохранение.

    Один браузер на сессию. Сессии ключуются либо сгенерированным id (мастер
    подключения), либо id подключения Meta (повторный вход и автообновление
    токена) — во втором случае сохранённый storage_state ложится в файл
    <connection_id>.json, по которому синхронизация восстановит сессию сама.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, MetaSessionState] = {}
        self._lock = asyncio.Lock()
        self._session_dir = Path(settings.meta_session_dir)
        self._session_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Внутренние хелперы ----------

    def _session_file(self, session_id: str) -> Path:
        return self._session_dir / f"{session_id}.json"

    def _fingerprint(self, user_agent: str | None) -> dict:
        return {
            "userAgent": user_agent or None,
            "viewport": {
                "width": settings.meta_browser_viewport_width,
                "height": settings.meta_browser_viewport_height,
            },
            "timezoneId": settings.meta_browser_timezone,
            "locale": settings.meta_browser_locale,
            "languageHeader": settings.meta_browser_language_header,
        }

    async def _launch_browser(self, pw):
        """Браузер с антидетект-флагами. Канал из настроек с откатом на Chromium."""
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        if not settings.meta_browser_headless:
            launch_args.append(
                f"--window-size={settings.meta_browser_viewport_width},"
                f"{settings.meta_browser_viewport_height}"
            )
        channel = settings.meta_browser_channel or None
        try:
            browser = await pw.chromium.launch(
                headless=settings.meta_browser_headless,
                channel=channel,
                args=launch_args,
            )
            if channel:
                logger.info("browser launched via channel=%s", channel)
            return browser
        except Exception as exc:
            if channel:
                logger.warning(
                    "channel=%s unavailable (%s), falling back to bundled Chromium", channel, exc
                )
                return await pw.chromium.launch(
                    headless=settings.meta_browser_headless, args=launch_args
                )
            raise

    async def _resolve_proxy(self, proxy_cfg: dict | None):
        """SOCKS5 с авторизацией Chromium не умеет — туннелируем через мост."""
        if not proxy_cfg:
            return None, None
        server = proxy_cfg.get("server") or ""
        if not server.startswith("socks5://"):
            return proxy_cfg, None
        username = proxy_cfg.get("username")
        password = proxy_cfg.get("password")
        if not username or not password:
            return proxy_cfg, None
        host_part = server[len("socks5://"):]
        if host_part.startswith("[") and "]" in host_part:
            # IPv6: "[::1]:1080"
            host, _, port = host_part[1:].partition("]")
            port = port.lstrip(":")
        elif ":" in host_part:
            host, _, port = host_part.rpartition(":")
        else:
            host, port = host_part, ""
        if not host or not port.isdigit():
            raise MetaSessionError(f"Некорректный адрес SOCKS5-прокси: {server}")
        from app.services.meta_socks_bridge import Socks5Bridge

        bridge = Socks5Bridge(host, int(port), username, password)
        await bridge.start()
        logger.info(
            "socks5 proxy has auth -> local bridge %s (Playwright gets plain HTTP proxy)",
            bridge.url,
        )
        return {"server": bridge.url}, bridge

    async def _is_logged_in(self, ctx) -> bool:
        try:
            cookies = await ctx.cookies()
            return any(c["name"] in {"c_user", "xs"} for c in cookies)
        except Exception:
            return False

    # ---------- Основные операции ----------

    async def start(
        self,
        *,
        session_id: str | None = None,
        cookies: str = "",
        proxy_url: str,
        user_agent: str | None = None,
    ) -> MetaSessionState:
        """Запускает headful-браузер с cookies аккаунта и его прокси.

        :param session_id: id сессии. Для мастера — не задан (генерируется);
            для повторного входа существующего подключения — его UUID, тогда
            сохранённая сессия ляжет в <connection_id>.json для автообновления.
        :param cookies:    сырой текст cookies из браузера.
        :param proxy_url:  ОБЯЗАТЕЛЕН. Без работающего прокси браузер не стартует —
            это защита аккаунта от бана.
        """
        session_id = session_id or f"meta_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        if session_id in self.sessions:
            existing = self.sessions[session_id]
            if existing.status not in {"error"}:
                return existing

        state = MetaSessionState(id=session_id, proxy_url=proxy_url, user_agent=user_agent)
        async with self._lock:
            self.sessions[session_id] = state

        # ==== КРИТИЧЕСКАЯ ЗАЩИТА ОТ БАНА ====
        # Прокси обязателен и проверяется ДО запуска браузера: cookies, показанные
        # чужому IP, почти гарантированно помечают сессию как угнанную.
        if not (proxy_url or "").strip():
            raise MetaSessionError(
                "Токен сессии (EAAB) требует прокси: браузер без него не запускается. "
                "Укажите прокси кабинета — тот, с которого вы обычно заходите в Ads Manager."
            )
        proxy_cfg = parse_proxy_url(proxy_url)
        try:
            check = await check_proxy_url(proxy_url)
            if not check.get("ok"):
                raise MetaSessionError(
                    f"Прокси не работает — запуск браузера отменён, чтобы защитить аккаунт "
                    f"от бана. Ошибка: {check.get('error')}"
                )
            logger.info(
                "[%s] proxy OK (%s, %sms)", session_id, check.get("ip"), check.get("latency_ms")
            )
            proxy_cfg, bridge = await self._resolve_proxy(proxy_cfg)
            state.bridge = bridge

            from playwright.async_api import async_playwright

            state.status = "starting"
            state.pw = pw = await async_playwright().start()
            state.browser = await self._launch_browser(pw)
            state.fingerprint = self._fingerprint(user_agent)

            ctx = await state.browser.new_context(
                viewport={
                    "width": settings.meta_browser_viewport_width,
                    "height": settings.meta_browser_viewport_height,
                },
                user_agent=user_agent or None,
                locale=settings.meta_browser_locale,
                timezone_id=settings.meta_browser_timezone,
                proxy=proxy_cfg,
                extra_http_headers={"Accept-Language": settings.meta_browser_language_header},
            )
            await ctx.add_init_script(STEALTH_INIT_SCRIPT)
            state.context = ctx

            parsed = parse_cookies(cookies)
            if parsed:
                await ctx.add_cookies(parsed)
                logger.info("[%s] %d cookies injected", session_id, len(parsed))

            page = await ctx.new_page()
            state.page = page
            page.set_default_timeout(settings.meta_page_timeout_ms)

            if parsed:
                try:
                    await page.goto(
                        f"{settings.meta_facebook_url}/", wait_until="domcontentloaded"
                    )
                except Exception:
                    # Через прокси первый заход бывает медленным: FB тянет
                    # тяжёлый HTML, прокси душит параллельные соединения
                    # браузера. Во второй попытке достаточно дождаться ответа
                    # сервера (`commit`): авторизацию ниже всё равно проверяем
                    # по cookies, а ожидание всего DOM повторно давало тот же
                    # 45-секундный таймаут.
                    logger.warning(
                        "[%s] first goto timed out — retrying until response", session_id
                    )
                    await page.goto(
                        f"{settings.meta_facebook_url}/", wait_until="commit"
                    )
                await asyncio.sleep(random.uniform(1.5, 3.0))
                if await self._is_logged_in(ctx):
                    logger.info("[%s] cookies are valid — session authorized", session_id)
                    state.status = "saved"
                    await self.save_session(session_id)
                    asyncio.create_task(self._extract_and_notify(state))
                    return state
                state.status = "waiting_login"
                url = page.url
                if "checkpoint" in url or "captcha" in url:
                    state.error = "Facebook требует подтверждения (капча/checkpoint)."
                elif "login" in url:
                    state.error = "Cookies невалидны — требуется ручной вход."
                else:
                    state.error = "Сессия с cookies не авторизовалась — требуется ручной вход."
                logger.warning("[%s] %s (url=%s)", session_id, state.error, url)
                await page.goto(settings.meta_facebook_login_url, wait_until="domcontentloaded")
            else:
                logger.info("[%s] navigating to %s", session_id, settings.meta_facebook_login_url)
                await page.goto(settings.meta_facebook_login_url, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1.0, 2.5))
                state.status = "waiting_login"

            # Фоновая задача: ждём вход, затем сохраняем сессию и извлекаем токен.
            asyncio.create_task(self._wait_for_login(state))
            return state
        except MetaSessionError:
            raise
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)
            logger.exception("[%s] failed to start session", session_id)
            await self._cleanup(state)
            # Не отдаём человеку внутренний Call log Playwright. Проверка IP
            # могла пройти, но конкретно facebook.com этот прокси не открыл —
            # это отдельная и понятная причина, с которой можно работать.
            if "Page.goto" in str(exc) and "Timeout" in str(exc):
                raise MetaSessionError(
                    "Facebook не открылся через указанный прокси за 45 секунд. "
                    "Проверьте доступ к facebook.com через этот прокси или замените его."
                ) from exc
            raise

    async def _wait_for_login(self, state: MetaSessionState) -> None:
        """Ждёт авторизации, автосохраняет сессию, затем извлекает токен.

        Признаки входа: редирект с /login на главную/adsmanager ИЛИ появление
        cookies c_user / xs.
        """
        deadline = time.time() + settings.meta_login_timeout_sec
        logger.info("[%s] polling for login state...", state.id)

        while time.time() < deadline:
            await asyncio.sleep(3)
            page = state.page
            if page is None or page.is_closed():
                state.status = "error"
                state.error = "Страница браузера была закрыта"
                return
            try:
                url = page.url
                cookies = await state.context.cookies()
                logged = (
                    any(c["name"] in {"c_user", "xs"} for c in cookies)
                    or ("/login" not in url and "facebook.com" in url)
                )
                if logged:
                    logger.info("[%s] login detected, saving session...", state.id)
                    await self.save_session(state.id)
                    logger.info("[%s] session saved, extracting EAAB token...", state.id)
                    asyncio.create_task(self._extract_and_notify(state))
                    return
            except Exception as exc:
                logger.warning("[%s] login poll error: %s", state.id, exc)

        state.status = "error"
        state.error = "Login timeout — требуется ручное вмешательство (возможно капча)."
        logger.warning("[%s] %s", state.id, state.error)

    async def _extract_and_notify(self, state: MetaSessionState) -> None:
        """Фоновая задача: извлечение EAAB-токена после автосохранения."""
        try:
            result = await self.extract_token(state.id)
            if result.get("token"):
                state.token = result["token"]
                state.status = "token"
            logger.info(
                "[%s] token extraction done: %s",
                state.id,
                "FOUND" if result.get("token") else "not found",
            )
        except Exception as exc:
            logger.warning("[%s] token extraction failed: %s", state.id, exc)

    async def save_session(self, session_id: str) -> dict:
        """Экспортирует сессию: storage_state (cookies+localStorage) + fingerprint."""
        state = self.sessions.get(session_id)
        if not state or not state.context:
            raise ValueError(f"Session {session_id} not found or not running")

        await asyncio.sleep(random.uniform(0.5, 1.5))  # антидетект: без мгновенных действий

        storage = await state.context.storage_state()
        data = {
            "id": session_id,
            "saved_at": datetime.now(UTC).isoformat(),
            "fingerprint": state.fingerprint,
            "proxy_url": state.proxy_url,
            "user_agent": state.user_agent,
            "storage_state": storage,
        }
        path = self._session_file(session_id)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        state.status = "saved"
        state.saved_at = datetime.now(UTC)
        logger.info("[%s] session exported to %s", session_id, path)
        return {"session_id": session_id, "status": "saved", "path": str(path)}

    async def restore(
        self,
        session_id: str,
        *,
        proxy_url: str,
        user_agent: str | None = None,
        extract_token_in_background: bool = True,
    ) -> MetaSessionState:
        """Восстанавливает сессию из JSON: накатывает cookies/localStorage, проверяет вход.

        Используется автообновлением токена: синхронизация нашла смерть EAAB
        (190/102), восстановила браузерную сессию и извлекла новый токен.
        Прокси обязателен и проверяется так же строго, как при первом запуске.
        """
        path = self._session_file(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found on disk")
        data = json.loads(path.read_text(encoding="utf-8"))
        storage_state = data.get("storage_state")
        if not storage_state:
            raise MetaSessionError(
                f"Session {session_id}: файл сессии повреждён (нет storage_state). "
                "Удалите файл и создайте сессию заново."
            )
        fp = data.get("fingerprint") or {}

        state = self.sessions.get(session_id)
        if (
            state
            and state.context
            and state.status in {"waiting_login", "saved", "token", "starting"}
        ):
            return state
        state = MetaSessionState(
            id=session_id, status="restoring", proxy_url=proxy_url, user_agent=user_agent
        )
        self.sessions[session_id] = state
        state.fingerprint = fp

        try:
            check = await check_proxy_url(proxy_url)
            if not check.get("ok"):
                raise MetaSessionError(
                    f"Прокси не работает — восстановление сессии отменено, чтобы защитить "
                    f"аккаунт от бана. Ошибка: {check.get('error')}"
                )
            proxy_cfg, bridge = await self._resolve_proxy(parse_proxy_url(proxy_url))
            state.bridge = bridge

            from playwright.async_api import async_playwright

            state.pw = pw = await async_playwright().start()
            state.browser = await self._launch_browser(pw)
            ctx = await state.browser.new_context(
                storage_state=storage_state,
                viewport=fp.get("viewport"),
                user_agent=user_agent or fp.get("userAgent"),
                locale=fp.get("locale"),
                timezone_id=fp.get("timezoneId"),
                proxy=proxy_cfg,
                extra_http_headers={"Accept-Language": settings.meta_browser_language_header},
            )
            await ctx.add_init_script(STEALTH_INIT_SCRIPT)
            state.context = ctx

            page = await ctx.new_page()
            state.page = page
            page.set_default_timeout(settings.meta_page_timeout_ms)

            await page.goto(f"{settings.meta_facebook_url}/", wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

            if await self._is_logged_in(ctx):
                state.status = "saved"
                logger.info("[%s] session restored successfully", session_id)
                if extract_token_in_background:
                    asyncio.create_task(self._extract_and_notify(state))
                return state

            state.status = "waiting_login"
            state.error = "Session expired or account banned. Повторный вход требуется."
            logger.warning("[%s] %s", session_id, state.error)
            await page.goto(settings.meta_facebook_login_url, wait_until="domcontentloaded")
            asyncio.create_task(self._wait_for_login(state))
            return state
        except MetaSessionError:
            raise
        except Exception as exc:
            state.status = "error"
            state.error = str(exc)
            logger.exception("[%s] restore failed", session_id)
            await self._cleanup(state)
            raise

    async def extract_token(self, session_id: str) -> dict:
        """Извлекает EAAB-токен из живой сессии.

        Стратегии по порядку:
          1. localStorage ключи, содержащие EAAB;
          2. window.___fbConfig (глобальный конфиг, в котором часто лежит токен);
          3. DOM-страница adsmanager.facebook.com (regex EAAB...);
          4. Перехват GraphQL-запросов (response body с EAAB);
          5. Повторная попытка ___fbConfig после всех навигаций.

        Facebook сам редиректит страницу (чекпойнты, региональные перебросы),
        из-за чего наши goto/reload падают с ERR_ABORTED, а evaluate — с
        «Execution context was destroyed». Поэтому навигация повторяется с
        паузами, а параллельные вызовы сериализуются локом сессии.
        """
        state = self.sessions.get(session_id)
        if not state or not state.context:
            raise ValueError(f"Session {session_id} not found or not running")
        if state.token:
            return {
                "session_id": session_id,
                "token": state.token,
                "methods_tried": list(state.methods_tried),
            }
        if state.extract_lock is None:
            state.extract_lock = asyncio.Lock()
        async with state.extract_lock:
            # Пока ждали лока, параллельный вызов мог уже найти токен.
            if state.token:
                return {
                    "session_id": session_id,
                    "token": state.token,
                    "methods_tried": list(state.methods_tried),
                }
            page = state.page
            if not page or page.is_closed():
                raise MetaSessionError(
                    "Браузер сессии закрыт — запустите сессию заново и получите токен."
                )
            token = None
            methods_tried: list[str] = []

            def alive() -> bool:
                return bool(page) and not page.is_closed()

            # 1) localStorage
            try:
                methods_tried.append("localStorage")
                await self._goto_retry(page, f"{settings.meta_facebook_url}/")
                await self._settle(page)
                lv = await self._evaluate_retry(
                    page, "() => Object.entries(localStorage)"
                )
                for _key, value in lv:
                    if "EAAB" in str(value):
                        m = EAAB_TOKEN_RE.search(str(value))
                        if m:
                            token = m.group(0)
                            break
            except Exception as exc:
                logger.warning("[%s] localStorage extraction failed: %s", session_id, exc)

            # 2) window.___fbConfig
            if not token and alive():
                try:
                    methods_tried.append("fbConfig")
                    token = await self._evaluate_retry(page, FBCONFIG_TOKEN_JS)
                except Exception as exc:
                    logger.warning("[%s] fbConfig extraction failed: %s", session_id, exc)

            # 3) DOM adsmanager
            if not token and alive():
                try:
                    methods_tried.append("dom")
                    await self._goto_retry(page, settings.meta_ads_manager_url)
                    await self._settle(page, 3.0)
                    content = await page.content()
                    m = EAAB_TOKEN_RE.search(content)
                    token = m.group(0) if m else None
                except Exception as exc:
                    logger.warning("[%s] DOM extraction failed: %s", session_id, exc)

            # 4) Перехват GraphQL-ответов
            if not token and alive():
                methods_tried.append("graphql")
                try:
                    token = await self._capture_graphql_token(page, session_id)
                except Exception as exc:
                    logger.warning("[%s] GraphQL extraction failed: %s", session_id, exc)

            # 5) Финальная попытка: после всех навигаций страница улеглась —
            # спросим fbConfig ещё раз, не трогая её.
            if not token and alive():
                try:
                    token = await self._evaluate_retry(page, FBCONFIG_TOKEN_JS)
                    if token:
                        methods_tried.append("fbConfig-retry")
                except Exception:
                    pass

            if not alive():
                raise MetaSessionError(
                    "Браузер сессии закрыт во время извлечения — запустите сессию заново."
                )
            if token:
                state.token = token
                state.methods_tried = list(methods_tried)
            logger.info(
                "[%s] token extraction methods tried: %s -> %s",
                session_id,
                methods_tried,
                "FOUND" if token else "NOT FOUND",
            )
            return {"session_id": session_id, "token": token, "methods_tried": methods_tried}

    async def _goto_retry(self, page, url: str, *, attempts: int = 3) -> None:
        """goto с повторами: во время собственных редиректов Facebook наш
        переход отменяется (ERR_ABORTED) — повтор после паузы проходит."""
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded")
                return
            except Exception as exc:
                last = exc
                if attempt < attempts:
                    await asyncio.sleep(random.uniform(1.5, 3.0))
        assert last is not None
        raise RuntimeError(f"goto failed after {attempts} attempts: {last}") from last

    async def _evaluate_retry(self, page, expression: str, *, attempts: int = 3) -> object:
        """evaluate с повторами: при навигации контекст уничтожается, и первый
        вызов падает — повтор после паузы обычно проходит."""
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await page.evaluate(expression)
            except Exception as exc:
                last = exc
                if attempt < attempts:
                    await asyncio.sleep(random.uniform(1.0, 2.0))
        assert last is not None
        raise RuntimeError(f"evaluate failed: {last}") from last

    async def _settle(self, page, seconds: float = 2.0) -> None:
        """Даём странице закончить собственные редиректы перед чтением."""
        try:
            await page.wait_for_load_state("load", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(seconds)

    async def _capture_graphql_token(self, page, session_id: str) -> str | None:
        found: list[str] = []

        async def on_response(response):
            try:
                if "graphql" in response.url or "adsmanager" in response.url:
                    body = await response.text()
                    if "EAAB" in body:
                        m = EAAB_TOKEN_RE.search(body)
                        if m:
                            found.append(m.group(0))
            except Exception:
                pass

        page.on("response", on_response)
        try:
            for attempt in range(2):
                if found:
                    break
                try:
                    await page.reload(wait_until="domcontentloaded")
                except Exception as exc:
                    logger.warning(
                        "[%s] GraphQL reload aborted (attempt %d): %s",
                        session_id,
                        attempt + 1,
                        exc,
                    )
                await asyncio.sleep(5.0)
        finally:
            page.remove_listener("response", on_response)
        return found[0] if found else None

    async def attach(self, session_id: str, connection_id: str) -> dict:
        """Привязывает сохранённую сессию мастера к подключению.

        Файл <session_id>.json копируется в <connection_id>.json — по нему
        синхронизация позже восстановит сессию и обновит токен сама.
        """
        source = self._session_file(session_id)
        if not source.exists():
            raise FileNotFoundError(f"Session {session_id} not found on disk")
        data = json.loads(source.read_text(encoding="utf-8"))
        data["id"] = connection_id
        target = self._session_file(connection_id)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[%s] session attached to connection %s", session_id, connection_id)
        return {"connection_id": connection_id, "status": "attached"}

    def get(self, session_id: str) -> MetaSessionState | None:
        return self.sessions.get(session_id)

    def live_transport(self, session_id: str) -> SessionHTTPTransport | None:
        """Транспорт живой сессии: запросы пойдут из её браузерного контекста.

        None, если сессии нет, браузер закрыт или вход ещё не выполнен.
        """
        state = self.sessions.get(session_id)
        if not state or not state.context:
            return None
        if state.status in {"saved", "token"}:
            return SessionHTTPTransport(state.context)
        return None

    def list_sessions(self) -> list[dict]:
        return [state.to_dict() for state in self.sessions.values()]

    async def close(self, session_id: str) -> None:
        state = self.sessions.get(session_id)
        if state:
            await self._cleanup(state)
            # Состояние удаляется полностью: иначе следующая публикация найдёт
            # state со status="saved", но context=None (браузер закрыт) и
            # восстановит «полумёртвый» транспорт — Meta жаловалась, что
            # «не отвечает по адресу», хотя дело было в обнулённом контексте.
            self.sessions.pop(session_id, None)
            logger.info("[%s] session closed", session_id)

    async def shutdown(self) -> None:
        for session_id in list(self.sessions.keys()):
            try:
                await self.close(session_id)
            except Exception:  # noqa: BLE001 — при остановке ошибки не важны
                pass

    async def _cleanup(self, state: MetaSessionState) -> None:
        """Гарантированная очистка ресурсов Playwright."""
        try:
            if state.page and not state.page.is_closed():
                await state.page.close()
        except Exception:
            pass
        try:
            if state.browser:
                await state.browser.close()
        except Exception:
            pass
        try:
            if state.pw:
                await state.pw.stop()
        except Exception:
            pass
        try:
            if state.bridge:
                await state.bridge.close()
        except Exception:
            pass
        state.page = None
        state.context = None
        state.browser = None
        state.pw = None
        state.bridge = None
        # Статус — в error: ветка «уже есть состояние» в restore проверяет его,
        # иначе полудохлое состояние могло бы выдать себя за живое.
        state.status = "error"
        state.error = "cleaned up"


# --- Singleton ---
_manager: MetaSessionManager | None = None


def get_session_manager() -> MetaSessionManager:
    global _manager
    if _manager is None:
        _manager = MetaSessionManager()
    return _manager


async def open_session_access(
    session_factory,
    connection_id: str,
    *,
    proxy_url: str | None,
    user_agent: str | None = None,
    store_token=None,
    refresh_token: bool = True,
) -> dict:
    """Живой транспорт браузерной сессии для запросов Meta + свежий EAAB.

    Для session-подключений: если сессия уже живёт в этом процессе — отдаёт её
    транспорт; иначе восстанавливает её из сохранённого файла (прокси обязателен,
    без него восстановление запрещено — защита аккаунта), извлекает свежий токен
    и сохраняет его в подключение (через session_factory или явный async-колбэк
    store_token — для вызовов из роутера, где фабрики нет).

    Возвращает {"transport": SessionHTTPTransport, "owned": bool, "token": str|None}.
    `owned=True` означает, что сессию восстановил этот вызов, и по завершении её
    нужно закрыть через manager.close(connection_id). Кидает MetaSessionError,
    если восстановление невозможно (нет файла/прокси/требуется вход).
    """
    manager = get_session_manager()
    state = manager.get(connection_id)
    if state and state.status in {"saved", "token"} and state.context:
        return {
            "transport": SessionHTTPTransport(state.context),
            "owned": False,
            "token": state.token,
        }
    if not (proxy_url or "").strip():
        raise MetaSessionError(
            "Подключение с токеном сессии не имеет прокси — восстановить "
            "браузерную сессию не из чего. Пересоздайте подключение через мастер."
        )
    session_file = Path(settings.meta_session_dir) / f"{connection_id}.json"
    if not session_file.exists():
        raise MetaSessionError(
            "Сохранённая браузерная сессия не найдена. Пройдите вход ещё раз "
            "через «Браузер Facebook» в подключении — сессия запишется на диск."
        )
    try:
        state = await manager.restore(
            connection_id,
            proxy_url=proxy_url,
            user_agent=user_agent,
            extract_token_in_background=refresh_token,
        )
    except Exception as exc:
        raise MetaSessionError(f"Не удалось восстановить браузерную сессию: {exc}") from exc
    if state.status == "waiting_login":
        raise MetaSessionError(
            "Сессия Facebook требует ручного входа. Подключитесь по VNC (порт 5900), "
            "войдите в аккаунт и повторите операцию."
        )
    token: str | None = None
    if refresh_token:
        try:
            result = await manager.extract_token(connection_id)
            token = result.get("token") or None
        except Exception as exc:
            logger.warning("[%s] token extraction during restore failed: %s", connection_id, exc)
    if token:
        state.token = token
        if store_token is not None:
            await store_token(token)
        elif session_factory is not None:
            from app.core.security import decrypt_secret, encrypt_secret
            from app.models import IntegrationConnection

            async with session_factory() as db:
                connection = await db.get(IntegrationConnection, uuid.UUID(connection_id))
                if connection and decrypt_secret(connection.api_key_encrypted) != token:
                    connection.api_key_encrypted = encrypt_secret(token)
                    await db.commit()
        logger.info("[%s] fresh session token stored", connection_id)
    return {
        "transport": SessionHTTPTransport(state.context),
        "owned": True,
        "token": token,
    }
