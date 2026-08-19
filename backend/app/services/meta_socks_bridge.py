"""Локальный HTTP→SOCKS5 мост для Playwright.

Проблема: Chromium НЕ поддерживает авторизацию (логин/пароль) для SOCKS5 —
Playwright падает с "Browser does not support socks5 proxy authentication"
(ограничение Chromium, открытое с 2021 года: microsoft/playwright#10567).

Решение: поднимаем локальный HTTP-прокси на 127.0.0.1 БЕЗ авторизации,
который сам туннелирует трафик через SOCKS5 с логином/паролем. Playwright
указываем на http://127.0.0.1:PORT — для него это обычный HTTP-прокси.

Реализация — чистый asyncio без внешних зависимостей:
  - CONNECT (HTTPS) — полный двунаправленный туннель;
  - обычные GET/POST (http://...) — проксируются через тот же туннель.
SOCKS5-рукопожатие реализовано по RFC 1928/1929 (greeting, auth, connect,
доменное имя резолвится на стороне прокси — rdns, без DNS-утечки).

Портировано из FBads (socks_bridge.py) — здесь оно нужно, потому что
подключения Meta Ads допускают socks5-прокси с авторизацией, а браузер
сессии должен ходить ровно тем же маршрутом, что и остальные запросы CRM.
"""

import asyncio
import logging
from urllib.parse import urlsplit

logger = logging.getLogger("meta_socks_bridge")


class Socks5AuthError(Exception):
    """Ошибка аутентификации/подключения к SOCKS5."""


class Socks5Bridge:
    """Локальный HTTP-прокси, туннелирующий через авторизованный SOCKS5."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.server: asyncio.AbstractServer | None = None
        self.local_port: int | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    async def start(self) -> int:
        """Запускает локальный сервер на 127.0.0.1:0 (случайный порт)."""
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        self.local_port = self.server.sockets[0].getsockname()[1]
        logger.info(
            "SOCKS5 bridge: %s -> socks5://%s:%d (auth via local relay)",
            self.url, self.host, self.port,
        )
        return self.local_port

    async def close(self) -> None:
        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass
            self.server = None
            logger.info("SOCKS5 bridge closed (%s)", self.url)

    # ---------- HTTP-обработка ----------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=60)
            if not request_line:
                return

            # Читаем заголовки до пустой строки, сохраняя сырые байты
            header_lines: list[bytes] = []
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=60)
                if line in (b"\r\n", b"\n", b""):
                    break
                header_lines.append(line)

            parts = request_line.decode("latin-1", "replace").split(" ", 2)
            if len(parts) < 3:
                return
            method, target = parts[0], parts[1]

            if method == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_plain(reader, writer, method, target, header_lines)
        except Exception as exc:
            logger.debug("bridge client error: %s", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_connect(self, reader, writer, target: str) -> None:
        """CONNECT host:port — полный туннель (HTTPS-трафик)."""
        host, _, port = target.rpartition(":")
        socks_reader, socks_writer = await self._socks_connect(host, int(port))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._relay(reader, writer, socks_reader, socks_writer)

    async def _handle_plain(self, reader, writer, method: str, target: str, header_lines: list[bytes]) -> None:
        """Обычный GET/POST в абсолютной форме: GET http://host/path HTTP/1.1."""
        split = urlsplit(target)
        host = split.hostname
        if not host:
            return
        port = split.port or (443 if split.scheme == "https" else 80)
        socks_reader, socks_writer = await self._socks_connect(host, port)

        # Восстанавливаем исходный запрос: строка + заголовки + тело (Content-Length)
        request = f"{method} {split.path or '/'}{'?' + split.query if split.query else ''} HTTP/1.1".encode("latin-1") + b"\r\n"
        request += b"".join(header_lines) + b"\r\n"
        content_length = 0
        for line in header_lines:
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    content_length = 0
        if content_length > 0:
            request += await asyncio.wait_for(reader.readexactly(content_length), timeout=60)
        socks_writer.write(request)
        await socks_writer.drain()
        await self._relay(reader, writer, socks_reader, socks_writer)

    # ---------- SOCKS5 (RFC 1928/1929) ----------

    async def _socks_connect(self, host: str, port: int):
        """Подключается к целевому хосту через SOCKS5 с авторизацией."""
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            # Greeting: v5, методы [no-auth, user/pass]
            writer.write(b"\x05\x01\x02")
            await writer.drain()
            resp = await reader.readexactly(2)
            if resp[1] == 0x02:
                # RFC 1929: user/pass auth
                u, p = self.username.encode(), self.password.encode()
                writer.write(bytes([0x01, len(u)]) + u + bytes([len(p)]) + p)
                await writer.drain()
                auth = await reader.readexactly(2)
                if auth[1] != 0x00:
                    raise Socks5AuthError("SOCKS5: неверные логин/пароль")
            elif resp[1] != 0x00:
                raise Socks5AuthError(
                    f"SOCKS5: сервер отверг методы аутентификации (код {resp[1]})"
                )

            # Connect request: домен резолвится НА ПРОКСИ (тип 0x03) — без DNS-утечки
            host_bytes = host.encode()
            writer.write(
                b"\x05\x01\x00\x03"
                + bytes([len(host_bytes)])
                + host_bytes
                + port.to_bytes(2, "big")
            )
            await writer.drain()

            head = await reader.readexactly(4)
            if head[1] != 0x00:
                raise Socks5AuthError(f"SOCKS5: подключение не удалось (код {head[1]})")
            atyp = head[3]
            if atyp == 0x01:
                await reader.readexactly(4)
            elif atyp == 0x04:
                await reader.readexactly(16)
            elif atyp == 0x03:
                ln = (await reader.readexactly(1))[0]
                await reader.readexactly(ln)
            await reader.readexactly(2)  # порт
            return reader, writer
        except Exception:
            # Ошибка рукопожатия не доходит до relay, поэтому закрывать этот
            # сокет больше некому; без закрытия сервер прокси держит задачу и
            # последующие проверки/перезапуски моста зависают.
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            raise

    # ---------- Релей ----------

    @staticmethod
    async def _relay(reader, writer, socks_reader, socks_writer) -> None:
        async def pump(src, dst):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

        await asyncio.gather(pump(reader, socks_writer), pump(socks_reader, writer))
