"""CLI: python -m ss14_ocr {run|edit-region|bench} (ТЗ §9).

  run          — живой конвейер (или --source folder для воспроизведения);
  edit-region  — внешний UI области чата (FR-4);
  bench        — CER/задержка/память по цепочкам предобработки и psm (FR-14).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger("cli")


def _load_store(config_dir: Path):
    from .config import load_app_config, load_layout_config
    from .config_watch import ConfigStore
    app = load_app_config(config_dir / "app.yaml")
    layout_path = config_dir / "layouts" / f"{app.layout}.yaml"
    layout = load_layout_config(layout_path)
    return ConfigStore(app, layout)


def cmd_run(args) -> int:
    from .app import Pipeline, setup_logging
    config_dir = Path(args.config)
    store = _load_store(config_dir)
    if args.source:
        app = store.app.model_copy(update={"capture": store.app.capture.model_copy(
            update={"backend": args.source, **({"folder": args.folder} if args.folder else {})})})
        store.apply_app(app)
    elif args.folder:
        app = store.app.model_copy(update={"capture": store.app.capture.model_copy(
            update={"backend": "folder", "folder": args.folder})})
        store.apply_app(app)
    setup_logging(store.snapshot()[0], config_dir)
    pipeline = Pipeline(store, config_dir, debug_preview=args.debug)

    watcher = None
    try:
        from .config_watch import ConfigWatcher
        watcher = ConfigWatcher(config_dir, store)
        watcher.start()
    except Exception:
        log.exception("ConfigWatcher не запущен — горячее применение недоступно")

    try:
        pipeline.run()
        return 0
    except KeyboardInterrupt:                      # FR-15
        pipeline.request_stop()
        return 0
    finally:
        if watcher:
            watcher.stop()


def cmd_edit_region(args) -> int:
    from .ui.region_editor import main as er_main
    argv = ["--config", str(args.config)]
    if args.client_rect:
        argv += ["--client-rect", args.client_rect]
    return er_main(argv)


def cmd_bench(args) -> int:
    from .bench import run_bench
    return run_bench(Path(args.config), Path(args.fixtures), chains=args.chain)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ss14-ocr",
                                description="OCR-каркас для SS14-агента (только чтение экрана)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="запустить конвейер")
    r.add_argument("--config", default="config")
    r.add_argument("--source", choices=["wgc", "dxcam", "mss", "folder"], default=None,
                   help="переопределить capture.backend (FR-12)")
    r.add_argument("--folder", default=None, help="папка PNG при --source folder")
    r.add_argument("--debug", action="store_true", help="включить превью (FR-13)")
    r.set_defaults(func=cmd_run)

    e = sub.add_parser("edit-region", help="UI области чата (FR-4)")
    e.add_argument("--config", default="config")
    e.add_argument("--client-rect", default=None, help="x,y,w,h (отладка без окна)")
    e.set_defaults(func=cmd_edit_region)

    b = sub.add_parser("bench", help="бенчмарк OCR по golden-набору (FR-14)")
    b.add_argument("--config", default="config")
    b.add_argument("--fixtures", default="tests/fixtures/chat",
                   help="папка пар PNG+TXT")
    b.add_argument("--chain", action="append", default=None,
                   help="цепочка предобработки через запятую; можно несколько раз")
    b.set_defaults(func=cmd_bench)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
