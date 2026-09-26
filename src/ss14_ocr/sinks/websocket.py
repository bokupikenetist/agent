"""WebSocketSink: локальный WS-сервер для агента (FR-11, NFR-8).

Слушает только 127.0.0.1; каждому подключению приходит hello с историей логов,
далее — события и log_batch'и в формате:
  {"type": "hello",      "tail_lines": [...]}
  {"type": "event",      "event": {...}}          # TextEvent и будущие типы
  {"type": "log_batch",  "records": [{"level","line"}, ...]}
Старый «сырой» формат (событие без обёртки) включается флагом raw_events=True
(обратная совместимость tools/ws_client.py). Отвалившихся клиентов вычищаем;
ошибки отправки логируются, конвейер не роняем.

Дополнительно к событиям умеет отдавать в браузер:
  GET /            — страница «Логи SS14-OCR» (живая лента логов + события);
  GET /logs.jsonl  — дамп файла лога (text/plain).
Логирование в WS настраивается секцией sinks.websocket.logging:
  enabled — пересылать лог-записи подключённым клиентам;
  tail_file — при подключении прислать хвост файла лога (историю).
"""
from __future__ import annotations

import collections
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_LOG_RECORDS = 500          # буфер последних лог-записей для истории
TAIL_LINES = 300               # строк файла лога отдаём при подключении


class WsLogHandler(logging.Handler):
    """Пишет лог-записи в буфер sink'а; рассылку делает сам sink (см. pump)."""

    def __init__(self, sink: "WebSocketSink"):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == "websockets":       # служебный лог сервера — без рекурсии
                return
            self.sink.enqueue_log(self.format(record), getattr(record, "levelname", "INFO"))
        except Exception:
            pass                                   # никогда не роняем логирование


class WebSocketSink:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, *,
                 log_enabled: bool = False, tail_file: bool = True,
                 log_file: str | Path | None = None, raw_events: bool = False):
        self.host = host
        self.port = port
        self.log_enabled = log_enabled
        self.tail_file = tail_file
        self.raw_events = raw_events
        self.log_file = Path(log_file) if log_file else None
        self._clients: set = set()
        self._server = None
        self._log_records: collections.deque = collections.deque(maxlen=MAX_LOG_RECORDS)
        self._pending_logs: list[str] = []
        self._log_handler: WsLogHandler | None = None

    # ---------- lifecycle ----------
    async def start(self) -> None:
        import asyncio

        from websockets.exceptions import ConnectionClosed
        from websockets.http11 import Response

        try:                                # websockets >= 13
            from websockets.asyncio.server import serve
        except ImportError:                 # websockets 10–12
            from websockets import serve    # type: ignore[attr-defined,no-redef]

        async def process_request(connection, request):
            """HTTP-ответы браузера до handshake: страница и дамп лога."""
            headers = getattr(request, "headers", None) or {}
            head = getattr(headers, "get", None) or (lambda k, d=None: d)
            path = (getattr(request, "path", None) or "/").split("?")[0]
            if head("Upgrade", "").lower() == "websocket":
                return None
            if path in ("/", "/index.html"):
                body = self._index_html().encode("utf-8")
                return Response(200, "OK", {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Length": str(len(body)),
                }, body)
            if path == "/logs.jsonl":
                text = ""
                if self.log_file and self.log_file.exists():
                    text = self.log_file.read_text(encoding="utf-8", errors="replace")
                body = text.encode("utf-8")
                return Response(200, "OK", {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Content-Length": str(len(body)),
                }, body)
            return None                     # остальное — обычный WS-handshake

        async def handler(ws):
            self._clients.add(ws)
            log.info("WS-клиент подключился: %s (всего %d)", ws.remote_address, len(self._clients))
            try:
                hello = {"type": "hello", "tail_lines": TAIL_LINES,
                         "history": self.history()}
                await ws.send(json.dumps(hello, ensure_ascii=False))
                async for raw in ws:        # ping от браузера — отвечаем, держим NAT/прокси
                    if isinstance(raw, str) and raw.strip() == "ping":
                        await ws.send("pong")
            except ConnectionClosed:
                pass
            except Exception:
                log.debug("WS-клиент отвалился с ошибкой", exc_info=True)
            finally:
                self._clients.discard(ws)

        kwargs = {}
        try:
            self._server = await serve(handler, self.host, self.port,
                                       process_request=process_request, **kwargs)
        except TypeError:                   # старый API: http_handler вместо process_request
            self._server = await serve(handler, self.host, self.port)
        log.info("WebSocket-сервер слушает ws://%s:%d (логи в браузере: http://%s:%d/)",
                 self.host, self.port, self.host, self.port)
        if self.log_enabled:
            self._attach_log_handler()
        self._bcast_task = asyncio.create_task(self._broadcast_loop())

    def _attach_log_handler(self) -> None:
        if self._log_handler is not None:
            return
        root = logging.getLogger()
        h = WsLogHandler(self)
        h.setLevel(root.level if root.level else logging.INFO)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        h._ss14_ws_sink = self          # чтобы setup_logging переподключил нас при reload
        root.addHandler(h)
        self._log_handler = h
        log.info("Пересылка логов в WebSocket включена (буфер %d записей)", MAX_LOG_RECORDS)

    def attach_log_handler(self) -> None:
        """Публичный алиас для hot reload из setup_logging."""
        self._attach_log_handler()

    def detach_log_handler(self) -> None:
        """Снять handler с root (вызывается при hot reload и остановке)."""
        if self._log_handler is not None:
            h = self._log_handler
            self._log_handler = None
            try:
                logging.getLogger().removeHandler(h)
                h.close()
            except Exception:
                pass

    # ---------- лог-поток ----------
    def enqueue_log(self, line: str, level: str = "INFO") -> None:
        """Из любого потока: кладём запись в буфер и очередь на отправку."""
        rec = {"level": level, "line": line}
        self._log_records.append(rec)
        self._pending_logs.append(rec)

    def history(self) -> list[dict]:
        """Последние лог-записи: хвост файла (если есть) или in-memory буфер."""
        if self.tail_file and self.log_file and self.log_file.exists():
            try:
                text = self.log_file.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()[-TAIL_LINES:]
                return [{"level": _level_of(l), "line": l} for l in lines]
            except Exception:
                log.debug("не удалось прочитать хвост лога", exc_info=True)
        return list(self._log_records)

    async def _broadcast_loop(self) -> None:
        import asyncio
        while True:
            await asyncio.sleep(0.2)
            if not self._pending_logs or not self._clients:
                if not self._pending_logs:
                    continue
            batch, self._pending_logs = self._pending_logs[:], []
            payload = {"type": "log_batch", "records": batch}
            await self._send_to_all(payload)

    # ---------- события ----------
    async def publish(self, event: dict) -> None:
        if self.raw_events:               # обратная совместимость: сырой TextEvent
            await self._send_to_all(event)
        else:
            await self._send_to_all({"type": "event", "event": event})

    async def _send_to_all(self, payload: dict) -> None:
        if not self._clients:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ---------- HTML-страница ----------
    def _index_html(self) -> str:
        ws_port = self.port
        return _INDEX_HTML.replace("__WS_PORT__", str(ws_port))

    async def close(self) -> None:
        bcast = getattr(self, "_bcast_task", None)
        if bcast:
            bcast.cancel()
        self.detach_log_handler()
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        log.info("WebSocket-сервер остановлен")


def _level_of(line: str) -> str:
    for lvl in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
        if f" {lvl} " in line[:40]:
            return lvl
    return "INFO"


_INDEX_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>SS14-OCR: логи</title>
<style>
 body{background:#111;color:#ddd;font:13px/1.45 Consolas,Menlo,monospace;margin:0}
 header{position:sticky;top:0;background:#1b1b1b;border-bottom:1px solid #333;
        padding:8px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 h1{font-size:14px;margin:0;color:#fff}
 .dot{width:9px;height:9px;border-radius:50%;background:#666;display:inline-block}
 .dot.on{background:#3c3}.off{background:#c33}
 label{color:#999;user-select:none;cursor:pointer}
 input[type=text]{background:#222;border:1px solid #444;color:#ddd;padding:3px 6px;
        border-radius:4px;width:220px}
 button{background:#2a2a2a;border:1px solid #444;color:#ddd;border-radius:4px;
        padding:3px 10px;cursor:pointer}
 #out{padding:8px 14px 40px;white-space:pre-wrap;word-break:break-word}
 .rec{padding:0 0 1px}
 .DEBUG{color:#667}.INFO{color:#cfd8dc}.WARNING{color:#ffb74d}
 .ERROR,.CRITICAL{color:#ef5350}
 .ev{color:#4fc3f7}.ev b{color:#81d4fa}
 #count{color:#666;margin-left:auto}
</style></head><body>
<header>
 <span class="dot" id="st"></span><h1>SS14-OCR · логи</h1>
 <label><input type="checkbox" id="fLog" checked> логи</label>
 <label><input type="checkbox" id="fEv" checked> события</label>
 <select id="lvl" style="background:#222;color:#ddd;border:1px solid #444">
  <option value="ALL">все уровни</option><option value="INFO">INFO+</option>
  <option value="WARNING">WARNING+</option><option value="ERROR">ERROR+</option>
 </select>
 <input type="text" id="q" placeholder="фильтр…">
 <button id="clr">очистить</button>
 <label><input type="checkbox" id="auto" checked> автопрокрутка</label>
 <span id="count"></span>
</header>
<div id="out"></div>
<script>
const out=document.getElementById('out'),st=document.getElementById('st');
let n=0,ws;
const LV={DEBUG:0,INFO:1,WARNING:2,ERROR:3,CRITICAL:4};
function lvlOf(r){return r.type==='event'?'EVENT':(r.level||'INFO')}
function ok(r){
 const l=lvlOf(r);
 if(l==='EVENT'){if(!fEv.checked)return false}
 else{
  if(!fLog.checked)return false;
  const m=document.getElementById('lvl').value;
  if(m!=='ALL'&&(LV[l]??1)<LV[m])return false;
 }
 const q=document.getElementById('q').value.toLowerCase();
 if(q&&!(r.text||'').toLowerCase().includes(q))return false;
 return true;
}
function render(r){
 const d=document.createElement('div');
 if(r.type==='event'){
  d.className='rec ev';
  d.innerHTML='<b>[событие]</b> '+esc(r.text||'');
 }else{
  d.className='rec '+(r.level||'INFO');
  d.textContent=r.text||'';
 }
 out.appendChild(d);n++;
 document.getElementById('count').textContent=n+' записей';
 if(document.getElementById('auto').checked)out.scrollTop=out.scrollHeight;
 while(out.childElementCount>3000)out.removeChild(out.firstChild);
}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function connect(){
 ws=new WebSocket('ws://127.0.0.1:__WS_PORT__/');
 ws.onopen=()=>st.className='dot on';
 ws.onclose=()=>{st.className='dot off';setTimeout(connect,2000)};
 ws.onerror=()=>ws.close();
 ws.onmessage=e=>{
  let m;try{m=JSON.parse(e.data)}catch(_){return}
  if(m.type==='hello'){(m.history||[]).forEach(r=>{const rec={type:'log',level:r.level,text:r.line};if(ok(rec))render(rec)});return}
  if(m.type==='log_batch')m.records.forEach(r=>{r.type='log';if(ok(r))render(r)});
  else if(m.type==='event'){const r={...m.event,type:'event',text:m.event.text};if(ok(r))render(r)}
 };
}
setInterval(()=>{if(ws&&ws.readyState===1)ws.send('ping')},15000);
document.getElementById('clr').onclick=()=>{out.innerHTML='';n=0;
 document.getElementById('count').textContent=''};
connect();
</script></body></html>
"""
