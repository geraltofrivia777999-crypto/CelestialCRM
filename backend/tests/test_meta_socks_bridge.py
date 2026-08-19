"""Тесты SOCKS5→HTTP моста (app/services/meta_socks_bridge.py).

Проверяют полную цепочку: локальный HTTP-прокси без авторизации ->
авторизованный SOCKS5 (RFC 1929) -> целевой сервер. Всё на 127.0.0.1,
никаких внешних сетей. Зачем: Chromium не умеет авторизацию SOCKS5, поэтому
браузер сессии Meta получает обычный HTTP-прокси, а мост сам проходит
логин/пароль — иначе Playwright падает при запуске.
"""

import asyncio
import socket

from app.services.meta_socks_bridge import Socks5Bridge

# ---------- Мини-серверы для тестов ----------

async def _echo_handler(reader, writer):
    """Эхо-сервер: всё, что пришло, возвращает обратно."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    try:
        writer.close()
    except Exception:
        pass


class MiniSocks5Server:
    """Минимальный SOCKS5-сервер с user/pass-аутентификацией (RFC 1929)."""

    def __init__(self, username: str, password: str, target_host: str, target_port: int):
        self.username = username
        self.password = password
        self.target_host = target_host
        self.target_port = target_port
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            # Greeting: v5 + методы [no-auth, user/pass]
            _, nmethods = await reader.readexactly(2)
            methods = await reader.readexactly(nmethods)
            if 0x02 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return
            writer.write(b"\x05\x02")
            await writer.drain()

            # RFC 1929 auth
            _, ulen = await reader.readexactly(2)
            username = (await reader.readexactly(ulen)).decode()
            plen = (await reader.readexactly(1))[0]
            password = (await reader.readexactly(plen)).decode()
            if (username, password) != (self.username, self.password):
                writer.write(b"\x01\x01")
                await writer.drain()
                return
            writer.write(b"\x01\x00")
            await writer.drain()

            # Connect request
            _, _, _, atyp = await reader.readexactly(4)
            if atyp == 0x03:
                ln = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(ln)).decode()
            else:
                host = socket.inet_ntoa(await reader.readexactly(4))
            port = int.from_bytes(await reader.readexactly(2), "big")
            if (host, port) != (self.target_host, self.target_port):
                writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return

            target_reader, target_writer = await asyncio.open_connection(host, port)
            writer.write(
                b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + port.to_bytes(2, "big")
            )
            await writer.drain()

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

            await asyncio.gather(pump(reader, target_writer), pump(target_reader, writer))
        except Exception:
            pass
        finally:
            # Сервер в тесте обязан закрыть и неуспешное auth-соединение:
            # иначе wait_closed ждёт живой transport и маскирует результат.
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


# ---------- Тесты моста ----------

async def _run_bridge_chain(test_echo: bool = True, wrong_password: bool = False):
    echo_server = await asyncio.start_server(_echo_handler, "127.0.0.1", 0)
    echo_port = echo_server.sockets[0].getsockname()[1]

    socks = MiniSocks5Server("user1", "pass1", "127.0.0.1", echo_port)
    socks_port = await socks.start()

    password = "WRONG" if wrong_password else "pass1"
    bridge = Socks5Bridge("127.0.0.1", socks_port, "user1", password)
    await bridge.start()
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", bridge.local_port)
        writer.write(f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        try:
            resp = await asyncio.wait_for(reader.readline(), timeout=5)
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            if not wrong_password:
                raise AssertionError(
                    f"CONNECT не прошёл даже с верными кредами: {exc}"
                ) from exc
            return
        if wrong_password:
            # Мост закрыл соединение без ответа (Socks5AuthError)
            assert not resp, f"при неверных кредах не должно быть ответа: {resp!r}"
            return
        assert b"200" in resp, f"CONNECT failed: {resp}"

        # Остаток заголовков ответа CONNECT (до пустой строки)
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if test_echo:
            writer.write(b"ping-via-socks5")
            await writer.drain()
            echoed = await asyncio.wait_for(reader.read(15), timeout=5)
            assert echoed == b"ping-via-socks5"
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        await bridge.close()
        await socks.stop()
        echo_server.close()


def test_bridge_tunnels_through_authenticated_socks5():
    """Полная цепочка: CONNECT через мост -> socks5+auth -> эхо-сервер."""
    asyncio.run(_run_bridge_chain())


def test_bridge_rejects_wrong_credentials():
    """Неверный логин/пароль — соединение не проходит."""
    asyncio.run(_run_bridge_chain(wrong_password=True))


def test_bridge_plain_http_request():
    """Обычный GET в абсолютной форме (http://) проходит через мост."""
    async def run():
        echo_server = await asyncio.start_server(_echo_handler, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        socks = MiniSocks5Server("u", "p", "127.0.0.1", echo_port)
        socks_port = await socks.start()

        bridge = Socks5Bridge("127.0.0.1", socks_port, "u", "p")
        await bridge.start()
        try:
            assert bridge.url == f"http://127.0.0.1:{bridge.local_port}"

            reader, writer = await asyncio.open_connection("127.0.0.1", bridge.local_port)
            writer.write(
                f"GET http://127.0.0.1:{echo_port}/hello HTTP/1.1\r\nHost: x\r\n\r\n".encode()
            )
            await writer.drain()
            data = await asyncio.wait_for(reader.read(200), timeout=5)
            assert b"GET /hello HTTP/1.1" in data
            writer.close()
        finally:
            await bridge.close()
            await socks.stop()
            echo_server.close()

    asyncio.run(run())


async def test_resolve_proxy_routes_authenticated_socks5_to_bridge(tmp_path, monkeypatch):
    """Менеджер сессий: socks5 с кредами → локальный мост, а не сырой socks5.

    Именно здесь решается проблема «Browser does not support socks5 proxy
    authentication»: браузер получает http://127.0.0.1:PORT без авторизации.
    """
    from app.services.meta_session import MetaSessionManager

    manager = MetaSessionManager()
    manager._session_dir = tmp_path
    cfg, bridge = await manager._resolve_proxy(
        {"server": "socks5://h:1080", "username": "u", "password": "p"}
    )
    try:
        assert bridge is not None
        assert cfg["server"].startswith("http://127.0.0.1:")
    finally:
        await bridge.close()


async def test_resolve_proxy_ipv6_creds_routes_to_bridge(tmp_path):
    """IPv6-хост в квадратных скобках не должен ронять разбор адреса."""
    from app.services.meta_session import MetaSessionManager

    manager = MetaSessionManager()
    manager._session_dir = tmp_path
    cfg, bridge = await manager._resolve_proxy(
        {"server": "socks5://[::1]:1080", "username": "u", "password": "p"}
    )
    try:
        assert bridge is not None
        assert cfg["server"].startswith("http://127.0.0.1:")
    finally:
        await bridge.close()


async def test_resolve_proxy_socks5_without_auth_passes_through(tmp_path):
    """SOCKS5 без логина/пароля Playwright поддерживает сам — мост не нужен."""
    from app.services.meta_session import MetaSessionManager

    manager = MetaSessionManager()
    manager._session_dir = tmp_path
    cfg, bridge = await manager._resolve_proxy({"server": "socks5://h:1080"})
    assert cfg == {"server": "socks5://h:1080"}
    assert bridge is None
