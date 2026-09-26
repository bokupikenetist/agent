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


async def listen(url: str, show_logs: bool = False) -> None:
    async with websockets.connect(url) as ws:
        print(f"подключено к {url}, ожидаю события...")
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                print("не-JSON:", raw[:120])
                continue
            # новый формат сервера: {"type": "hello"|"event"|"log_batch", ...}
            if isinstance(msg, dict) and msg.get("type") in ("hello", "event", "log_batch"):
                mtype = msg.get("type")
                if mtype == "hello":
                    if show_logs:
                        for r in msg.get("history", []):
                            print(r.get("line", ""))
                    continue
                if mtype == "log_batch":
                    if show_logs:
                        for r in msg.get("records", []):
                            print(r.get("line", ""))
                    continue
                if mtype == "event":
                    msg = msg.get("event", {})
                else:
                    continue
            ev = msg
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
    p.add_argument("--logs", action="store_true",
                   help="печатать и пересылаемые логи (история + поток)")
    args = p.parse_args()
    try:
        asyncio.run(listen(args.url, show_logs=args.logs))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
