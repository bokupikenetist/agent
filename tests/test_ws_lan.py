"""WebSocketSink в режиме локальной сети: host=0.0.0.0 слушает все интерфейсы,
страница и WS доступны по LAN-IP хоста (эмулируем другим локальным адресом)."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest

from ss14_ocr.sinks.websocket import WebSocketSink, lan_ips


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_lan_ips_returns_strings():
    ips = lan_ips()
    assert isinstance(ips, list)
    for ip in ips:
        socket.inet_aton(ip)                     # валидный IPv4
        assert not ip.startswith("127.")         # loopback не отдаём как LAN


@pytest.mark.asyncio
async def test_sink_on_all_interfaces_reachable_by_ip():
    """host=0.0.0.0 — «другая машина» (другой локальный IP) получает страницу и события."""
    port = _free_port()
    sink = WebSocketSink("0.0.0.0", port, log_enabled=False)
    await sink.start()
    try:
        # выбираем нетривиальный адрес хоста: первый LAN или любой непривязанный loopback
        candidates = lan_ips() or []
        if not candidates:
            candidates = ["127.0.0.2"]           # весь 127/8 принимает соединения как локальный
        host_addr = candidates[0]

        # HTTP-страница по IP
        reader, writer = await asyncio.open_connection(host_addr, port)
        writer.write(f"GET / HTTP/1.1\r\nHost: {host_addr}\r\nConnection: Upgrade\r\n"
                     f"Upgrade: websocket\r\nX-T: html-probe\r\n\r\n".encode())
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), timeout=5)
        headers = b""
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break
            headers += line
        writer.close()
        assert b"200" in status, status
        assert b"text/html" in headers          # процессор вернул HTML вместо handshake

        # WS-событие по тому же IP
        import websockets

        async with websockets.connect(f"ws://{host_addr}:{port}/") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert hello["type"] == "hello"
            assert "lan_urls" in hello
            await sink.publish({"region": "chat", "kind": "chat_line",
                                "text": "тест по сети"})
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg["type"] == "event"
            assert msg["event"]["text"] == "тест по сети"
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_loopback_host_has_no_lan_urls():
    port = _free_port()
    sink = WebSocketSink("127.0.0.1", port, log_enabled=False)
    await sink.start()
    try:
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert hello["lan_urls"] == []      # localhost-режим: сетевых адресов не показываем
    finally:
        await sink.close()
