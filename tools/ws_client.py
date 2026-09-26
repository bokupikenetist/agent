#!/usr/bin/env python3
"""Пример подписчика WebSocket для агента (FR-11).

python tools/ws_client.py [--url ws://127.0.0.1:8765]

Печатает каждое событие TextEvent одной строкой JSON; неизвестные поля
игнорируются (версионирование схемы — ТЗ §8). Заодно демонстрирует фильтр по
region/kind — агент-комментатор может брать только chat_line.
"""
import argparse
import asyncio
import json

import websockets


async def listen(url: str) -> None:
    async with websockets.connect(url) as ws:
        print(f"подключено к {url}, ожидаю события...")
        async for raw in ws:
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                print("не-JSON:", raw[:120])
                continue
            kind = ev.get("kind")
            if kind == "chat_line":
                print(f"[chat] {ev.get('text','')} (conf={ev.get('confidence')}, "
                      f"frame={ev.get('frame_id')}, ts={ev.get('ts')})")
            else:
                # будущие типы (moment и т.п.) — просто печатаем целиком
                print(f"[{kind or '?'}]", json.dumps(ev, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="ws://127.0.0.1:8765")
    args = p.parse_args()
    try:
        asyncio.run(listen(args.url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
