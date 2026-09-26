# ss14-ocr

OCR-каркас для SS14-агента: захватывает окно клиента Space Station 14, распознаёт
русский чат (Tesseract) и раздаёт структурированные `TextEvent` в консоль,
в JSONL-журнал сессии и по WebSocket. Только чтение экрана — никаких инъекций.

## Архитектура

```
capture (wgc | dxcam | mss | folder)
   └─> scheduler (поллинг, drop-old)
        └─> cropper (rect из layout)
             └─> preprocess (цепочка фильтров)
                  └─> OCR (tesseract, rus)
                       └─> ChatTracker (дедуп строк, anchored/unanchored)
                            └─> EventBus
                                 ├─> ConsoleSink
                                 ├─> SessionJournal → sessions/<ts>/events.jsonl
                                 └─> WebSocketSink → ws://host:8765 (+ веб-логи)
```

- **События** (`src/ss14_ocr/events.py`): `TextEvent(region, kind, text, ts, frame_id, engine, confidence, detection_quality)`. Схема версионируется добавлением полей в конец списка — читатели игнорируют неизвестные поля.
- **ChatTracker**: сверяет строки нового кадра с предыдущим экраном (rapidfuzz); помечает `detection_quality: unanchored`, если совпадений нет (полная прокрутка/сбой OCR).
- **Горячая перезагрузка конфига**: `watchdog` следит за `config/`, изменения применяются на лету без рестарта.

## Требования

- Python ≥ 3.11
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) с языковым пакетом `rus`
  (путь к бинарю задаётся в `config/app.yaml → ocr.tesseract.cmd`; если Tesseract в PATH — можно не указывать; при собственном каталоге данных задайте `TESSDATA_PREFIX`)
- Windows: рекомендуемый бэкенд захвата — `wgc` (Windows Graphics Capture, ставится как extra `windows`). На Linux/macOS используйте `mss` или `folder`.

## Установка

```bash
git clone <repo> && cd ss14-ocr
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # + extras windows: pip install -e ".[dev,windows]"
```

## Запуск

```bash
# живой конвейер (окно клиента ищется по window.title_regex)
ss14-ocr run

# воспроизведение со скриншотов из папки PNG (детерминированный прогон)
ss14-ocr run --source folder --folder path/to/pngs

# то же через модуль
python -m ss14_ocr run --config config --debug
```

Остановка — `Ctrl+C` (корректный сброс журнала и sinks).

### Команды CLI

| Команда | Назначение |
|---|---|
| `run` | запуск конвейера; флаги `--config`, `--source {wgc,dxcam,mss,folder}`, `--folder`, `--debug` |
| `edit-region` | визуальный редактор области чата (перетаскивание рамкой), пишет `rect` обратно в layout |
| `bench` | бенчмарк OCR (CER/задержка/память) по golden-набору `tests/fixtures/chat`, подбор цепочек предобработки и `psm` |

```bash
ss14-ocr edit-region
ss14-ocr bench --fixtures tests/fixtures/chat --chain upscale_3x,grayscale,autocontrast,invert
```

## Конфигурация

- `config/app.yaml` — окно, бэкенд захвата, OCR (язык, psm/oem, порог confidentности), журнал, sinks, логирование.
- `config/layouts/<name>.yaml` — геометрия областей (`rect` в долях клиентской области), частота опроса, порог изменения, цепочка предобработки.

Пример ключевых настроек:

```yaml
window:
  title_regex: "Space Station 14"
capture:
  backend: wgc            # wgc | dxcam | mss | folder
ocr:
  language: rus
  tesseract:
    cmd: ".\\tesseract\\tesseract.exe"
journal:
  dir: sessions           # sessions/<YYYY-MM-DD_HH-MM-SS>/events.jsonl
sinks:
  websocket:
    host: 0.0.0.0         # LAN-доступ; адреса печатаются в логе при старте
    port: 8765
```

## Куда пишутся расшифровки

| Куда | Что |
|---|---|
| `sessions/<YYYY-MM-DD_HH-MM-SS>/events.jsonl` | все `TextEvent` по одному JSON на строку (источник истины для агента) |
| консоль | человекочитаемый вывод каждого события |
| WebSocket `ws://<host>:8765` | поток событий в реальном времени |
| `logs/ss14_ocr.log` | служебные логи (уровень — `logging.level`) |

## WebSocket-протокол

Каждое сообщение — JSON-обёртка:

```json
{"type": "hello",     "tail_lines": [...]}
{"type": "event",     "event": { ...TextEvent... }}
{"type": "log_batch", "records": [{"level": "INFO", "line": "..."}]}
```

При подключении сервер шлёт `hello` с хвостом логов, далее — события и пачки логов.
Открытый `http://<ip>:8765/` отдаёт браузерную панель логов/событий. Сырой формат
события без обёртки включается флагом `raw_events=True` (обратная совместимость).

Пример подписчика:

```bash
python tools/ws_client.py --url ws://127.0.0.1:8765
```

## Тесты

```bash
pytest                     # unit + golden-тесты OCR по фикстурам tests/fixtures/chat
```

Golden-фикстуры (пары `chat_NN.png` + `chat_NN.txt`) генерируются скриптом
`tests/fixtures/make_fixtures.py`; для OCR-тестов нужны Tesseract и язык `rus`.

## Структура репозитория

```
config/            app.yaml + layouts/*.yaml
src/ss14_ocr/
  capture/         бэкенды захвата (wgc, dxcam, mss, folder, window)
  ocr/             движок Tesseract (image_to_data)
  sinks/           console, journal (JSONL), websocket
  ui/              редактор области чата
  app.py           сборка Pipeline; events.py — схема событий
  chat_tracker.py  дедупликация строк; bus.py — EventBus
  preprocess.py    фильтры; rects.py/regions.py — геометрия
  scheduler.py     поллинг кадров; config_watch.py — hot-reload
  bench.py         бенчмарк CER/задержки
tools/ws_client.py пример WS-подписчика для агента
tests/             pytest-тесты + fixtures
sessions/          JSONL-журналы сессий (создаются при запуске)
logs/              логи приложения
```

## Отладка

- Окно не находится — проверьте `window.title_regex` и что клиент запущен.
- Пустые/рваные строки — подберите `rect` через `edit-region`, увеличьте `min_word_conf` или поменяйте `psm` (6 обычно лучше для чата).
- Ничего не пишется в `sessions/` — смотрите `logs/ss14_ocr.log`: чаще всего не найден бинарь Tesseract или язык `rus` (проверка: `tesseract --list-langs`).
- Быстрый тест конвейера без игры: `ss14-ocr run --source folder --folder tests/fixtures/chat`.
