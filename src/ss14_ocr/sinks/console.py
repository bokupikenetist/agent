"""ConsoleSink: человекочитаемый вывод событий в консоль (FR-11)."""
from __future__ import annotations

import sys


class ConsoleSink:
    def __init__(self, stream=None):
        self._stream = stream or sys.stdout

    async def publish(self, event: dict) -> None:
        conf = event.get("confidence")
        conf_s = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
        self._stream.write(
            f"[{event.get('ts','')}] {event.get('region','?')}/{event.get('kind','?')} "
            f"f{event.get('frame_id','?')} ({event.get('engine','?')}){conf_s}: "
            f"{event.get('text','')}\n"
        )
        self._stream.flush()

    async def close(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
