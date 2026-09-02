"""Браузерные сессии Meta Ads: правила жизни состояния в памяти.

Воркер живёт дольше одного залива: закрытая сессия не должна выдавать себя
за живую, иначе следующий залив собрал бы транспорт с закрытым контекстом и
упал на «Meta не отвечает по адресу» (то самое: transport.context был None).
"""

import asyncio

import pytest

from app.services.meta_session import MetaSessionManager, MetaSessionState


def test_restore_ignores_zombie_state_without_context() -> None:
    """Состояние со status="saved", но context=None — не живое: restore
    должен уйти в пересоздание, а не вернуть полудохлое состояние."""
    manager = MetaSessionManager()
    zombie = MetaSessionState(
        id="zombie-1", status="saved", proxy_url="socks5://127.0.0.1:1"
    )
    zombie.context = None
    manager.sessions["zombie-1"] = zombie

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(FileNotFoundError):
            loop.run_until_complete(
                manager.restore("zombie-1", proxy_url="socks5://127.0.0.1:1")
            )
    finally:
        loop.close()


def test_close_forgets_the_state_entirely() -> None:
    """После close() в памяти не остаётся даже статуса: следующая публикация
    восстанавливает сессию с нуля, а не хватает артефакт."""
    manager = MetaSessionManager()
    state = MetaSessionState(
        id="gone-1", status="saved", proxy_url="socks5://127.0.0.1:1"
    )
    manager.sessions["gone-1"] = state

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(manager.close("gone-1"))
    finally:
        loop.close()

    assert "gone-1" not in manager.sessions
