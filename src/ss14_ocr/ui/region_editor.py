"""RegionEditor (FR-4): рамка области чата поверх окна клиента.

Внешний UI, а не оверлей внутри игры: отдельное прозрачное окно tkinter
поверх клиентской области SS14 + маленькое управляющее окошко (кнопки
«Сохранить»/«Отмена», координаты). В режиме «Правка» оверлей принимает мышь:
рамку двигают изнутри, размер меняют за углы. В режиме «Просмотр» рамка
полупрозрачна и не мешает игре (на Windows — click-through через
-transparentcolor).

Результат — только запись chat.rect в файл активной раскладки; конвейер
подхватит изменение через ConfigWatcher. На Linux запускается с --client-rect
для отладки без реального окна.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import load_app_config, load_layout_config, write_chat_rect

log = logging.getLogger("region_editor")


def run_edit_region(config_dir: Path, client_rect: tuple[int, int, int, int] | None = None) -> int:
    import tkinter as tk

    app_cfg = load_app_config(config_dir / "app.yaml")
    layout_path = config_dir / "layouts" / f"{app_cfg.layout}.yaml"
    layout_cfg = load_layout_config(layout_path)
    rect0 = list(layout_cfg.chat.rect)          # [x, y, w, h] доли

    if client_rect is None:
        if sys.platform != "win32":
            print("RegionEditor: нет окна клиента; на Linux укажите --client-rect x,y,w,h",
                  file=sys.stderr)
            return 2
        from ..capture.window import find_window, make_dpi_aware
        make_dpi_aware()
        info = find_window(app_cfg.window.title_regex, app_cfg.window.process_name)
        if info is None or info.minimized:
            print("RegionEditor: окно клиента не найдено/свёрнуто", file=sys.stderr)
            return 2
        client_rect = info.client_rect

    cx, cy, cw, ch = client_rect
    if cw <= 0 or ch <= 0:
        print("RegionEditor: нулевой клиентский прямоугольник", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.title("SS14 Region Editor")
    root.geometry("+{}+{}".format(cx + 16, max(0, cy - 90)))
    root.attributes("-topmost", True)

    # --- оверлей-рамка поверх клиента -------------------------------------
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    try:
        ov.attributes("-alpha", 0.55)
        ov.attributes("-transparentcolor", "magenta")   # Windows: магента прозрачна
        bg = "magenta"
    except tk.TclError:
        ov.attributes("-alpha", 0.30)                   # fallback: полупрозрачно целиком
        bg = "gray20"
    cv = tk.Canvas(ov, width=cw, height=ch, bg=bg, highlightthickness=0)
    cv.pack()
    ov.geometry(f"+{cx}+{cy}")

    state = {"rect": rect0[:], "drag": None, "interactive": False}

    def to_frac(px, py, pw, ph):
        return [px / cw, py / ch, pw / cw, ph / ch]

    def from_frac(r):
        return (int(r[0] * cw), int(r[1] * ch), int(r[2] * cw), int(r[3] * ch))

    def redraw(*_):
        cv.delete("all")
        x, y, w, h = from_frac(state["rect"])
        x2, y2 = x + w, y + h
        color = "#00ff88" if state["interactive"] else "#888888"
        cv.create_rectangle(x, y, x2, y2, outline=color, width=2)
        for hx, hy in ((x, y), (x2, y), (x, y2), (x2, y2)):
            cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill=color, outline=color)
        lbl.config(text=f"x={state['rect'][0]:.3f} y={state['rect'][1]:.3f} "
                        f"w={state['rect'][2]:.3f} h={state['rect'][3]:.3f}")

    def hit(px, py):
        x, y, w, h = from_frac(state["rect"])
        x2, y2 = x + w, y + h
        tol = 8
        if abs(px - x) < tol and abs(py - y) < tol: return "nw"
        if abs(px - x2) < tol and abs(py - y) < tol: return "ne"
        if abs(px - x) < tol and abs(py - y2) < tol: return "sw"
        if abs(px - x2) < tol and abs(py - y2) < tol: return "se"
        if x < px < x2 and y < py < y2: return "move"
        return None

    def on_press(e):
        if not state["interactive"]:
            return
        state["drag"] = (hit(e.x, e.y), e.x, e.y, state["rect"][:])

    def on_motion(e):
        if not state["interactive"] or not state["drag"]:
            return
        mode, sx, sy, r0 = state["drag"]
        if mode is None:
            return
        dx, dy = e.x - sx, e.y - sy
        x, y, w, h = from_frac(r0)
        if mode == "move":
            nx, ny = x + dx, y + dy
            nw, nh = w, h
        else:
            nx, ny, nw, nh = x, y, w, h
            if "n" in mode: ny, nh = min(y + dy, y + h - 10), max(10, h - dy)
            if "s" in mode: nh = max(10, h + dy)
            if "w" in mode: nx, nw = min(x + dx, x + w - 10), max(10, w - dx)
            if "e" in mode: nw = max(10, w + dx)
        nx = max(0, min(nx, cw - 10))
        ny = max(0, min(ny, ch - 10))
        nw = max(10, min(nw, cw - nx))
        nh = max(10, min(nh, ch - ny))
        state["rect"] = to_frac(nx, ny, nw, nh)
        redraw()

    def on_release(_):
        state["drag"] = None

    cv.bind("<ButtonPress-1>", on_press)
    cv.bind("<B1-Motion>", on_motion)
    cv.bind("<ButtonRelease-1>", on_release)

    def toggle():
        state["interactive"] = not state["interactive"]
        btn_mode.config(text="Режим: правка" if state["interactive"] else "Режим: просмотр")
        try:
            ov.attributes("-disabled", not state["interactive"])  # click-through в просмотре
        except tk.TclError:
            pass
        redraw()

    def save():
        write_chat_rect(layout_path, tuple(state["rect"]))
        log.info("chat.rect сохранён в %s: %s", layout_path, [round(v, 3) for v in state["rect"]])
        root.destroy()

    def cancel():
        root.destroy()

    frm = tk.Frame(root)
    frm.pack(padx=8, pady=8)
    btn_mode = tk.Button(frm, text="Режим: просмотр", command=toggle, width=16)
    btn_mode.grid(row=0, column=0, padx=4)
    tk.Button(frm, text="Сохранить", command=save, width=12).grid(row=0, column=1, padx=4)
    tk.Button(frm, text="Отмена", command=cancel, width=12).grid(row=0, column=2, padx=4)
    lbl = tk.Label(root, anchor="w")
    lbl.pack(fill="x", padx=8)
    tk.Label(root, fg="gray", text=(
        "«Правка» включает мышь по рамке: перемещение изнутри, размер за углы.\n"
        "Сохранить пишет chat.rect в активную раскладку; конвейер подхватит ≤2 с."
    )).pack(padx=8, pady=(0, 8))

    redraw()
    root.mainloop()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ss14-ocr edit-region", description=__doc__.splitlines()[0])
    p.add_argument("--config", default="config", help="папка конфигурации")
    p.add_argument("--client-rect", default=None,
                   help="x,y,w,h клиентской области (отладка без окна, Linux)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    cr = None
    if args.client_rect:
        cr = tuple(int(t) for t in args.client_rect.split(","))
        if len(cr) != 4:
            print("--client-rect: ожидается x,y,w,h", file=sys.stderr)
            return 2
    return run_edit_region(Path(args.config), cr)


if __name__ == "__main__":
    raise SystemExit(main())
