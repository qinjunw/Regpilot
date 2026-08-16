from __future__ import annotations

import threading
import webbrowser
from typing import Callable

import tkinter as tk
from tkinter import font as tkfont

from .launcher import RegPilotServerController


COLORS = {
    "bg": "#030b13",
    "grid": "#0c2237",
    "panel": "#071522",
    "panel_strong": "#091e31",
    "line": "#17304c",
    "line_hot": "#1aa99a",
    "text": "#e8f3ff",
    "muted": "#86a1ba",
    "green": "#46d19a",
    "amber": "#ff9f2f",
    "red": "#ff4168",
    "cyan": "#21d6c5",
    "button_text": "#031016",
}


class ActionButton(tk.Label):
    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        bg: str,
        fg: str,
        active_bg: str,
        font: tkfont.Font,
        width: int = 18,
    ) -> None:
        super().__init__(
            master,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            width=width,
            height=2,
            cursor="hand2",
            bd=0,
            padx=14,
            pady=2,
        )
        self._normal_bg = bg
        self._active_bg = active_bg
        self._command = command
        self.bind("<Enter>", lambda _event: self.configure(bg=self._active_bg))
        self.bind("<Leave>", lambda _event: self.configure(bg=self._normal_bg))
        self.bind("<Button-1>", lambda _event: self._command())


class RegPilotLauncherWindow:
    def __init__(self, controller: RegPilotServerController | None = None) -> None:
        self.controller = controller or RegPilotServerController()
        self.root = tk.Tk()
        self.root.title("RegPilot")
        self.root.geometry("620x420")
        self.root.minsize(620, 420)
        self.root.resizable(False, False)
        self.root.configure(bg=COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._stop_and_exit)

        self.title_font = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.heading_font = tkfont.Font(family="Microsoft YaHei UI", size=13, weight="bold")
        self.body_font = tkfont.Font(family="Microsoft YaHei UI", size=10)
        self.small_font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.button_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        self.canvas = tk.Canvas(self.root, width=620, height=420, highlightthickness=0, bg=COLORS["bg"])
        self.canvas.pack(fill="both", expand=True)
        self._draw_background()
        self._build_panel()

    def run(self) -> None:
        threading.Thread(target=self._start_server_worker, name="RegPilotLauncherStart", daemon=True).start()
        self.root.mainloop()

    def _draw_background(self) -> None:
        for x in range(0, 620, 28):
            self.canvas.create_line(x, 0, x, 420, fill=COLORS["grid"])
        for y in range(0, 420, 28):
            self.canvas.create_line(0, y, 620, y, fill=COLORS["grid"])
        self.canvas.create_rectangle(24, 24, 596, 396, fill=COLORS["panel"], outline=COLORS["line"], width=1)
        self.canvas.create_rectangle(24, 24, 596, 92, fill=COLORS["panel_strong"], outline=COLORS["line"])

    def _build_panel(self) -> None:
        self.canvas.create_rectangle(46, 42, 82, 78, fill=COLORS["cyan"], outline=COLORS["line_hot"], width=1)
        self.canvas.create_text(64, 60, text="R", fill=COLORS["bg"], font=("Segoe UI", 18, "bold"))
        self.canvas.create_text(102, 51, text="RegPilot", fill=COLORS["text"], font=self.title_font, anchor="w")
        self.canvas.create_text(
            104,
            76,
            text="本地法规智能体启动器",
            fill=COLORS["muted"],
            font=self.small_font,
            anchor="w",
        )

        self.status_chip = tk.Label(
            self.root,
            text="服务启动中",
            bg="#081b2d",
            fg=COLORS["amber"],
            font=self.small_font,
            padx=14,
            pady=7,
            bd=0,
        )
        self.canvas.create_window(470, 59, window=self.status_chip)

        self.canvas.create_rectangle(46, 118, 574, 262, fill="#061523", outline=COLORS["line"], width=1)
        self.canvas.create_text(66, 140, text="运行状态", fill=COLORS["text"], font=self.heading_font, anchor="w")
        self.status_title = tk.Label(
            self.root,
            text="正在启动本地服务",
            bg="#061523",
            fg=COLORS["text"],
            font=self.heading_font,
            anchor="w",
        )
        self.canvas.create_window(66, 172, window=self.status_title, anchor="w", width=360)
        self.status_detail = tk.Label(
            self.root,
            text="正在准备前端、后端和项目内 skills。",
            bg="#061523",
            fg=COLORS["muted"],
            font=self.body_font,
            anchor="w",
        )
        self.canvas.create_window(66, 202, window=self.status_detail, anchor="w", width=470)
        self.url_label = tk.Label(
            self.root,
            text="本地地址待生成",
            bg="#061523",
            fg="#bfe9ff",
            font=("Consolas", 10),
            anchor="w",
        )
        self.canvas.create_window(66, 232, window=self.url_label, anchor="w", width=470)

        self.open_button = ActionButton(
            self.root,
            text="打开 RegPilot",
            command=self._open_regpilot,
            bg=COLORS["cyan"],
            fg=COLORS["button_text"],
            active_bg="#6ff0a7",
            font=self.button_font,
        )
        self.canvas.create_window(154, 306, window=self.open_button)

        self.exit_button = ActionButton(
            self.root,
            text="停止并退出",
            command=self._stop_and_exit,
            bg="#10283f",
            fg=COLORS["text"],
            active_bg="#173a5c",
            font=self.button_font,
        )
        self.canvas.create_window(354, 306, window=self.exit_button)

        self.footer = tk.Label(
            self.root,
            text="模型 API key 不在发布包内。首次使用请在 RegPilot 设置中填写。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=self.small_font,
        )
        self.canvas.create_window(310, 362, window=self.footer)

    def _start_server_worker(self) -> None:
        try:
            url = self.controller.start()
        except Exception as exc:  # pragma: no cover - displayed in the launcher UI
            self.root.after(0, lambda: self._show_error(str(exc)))
            return
        self.root.after(0, lambda: self._show_ready(url))
        self.root.after(700, self._open_regpilot)

    def _show_ready(self, url: str) -> None:
        self.status_chip.configure(text="本地运行", fg=COLORS["green"])
        self.status_title.configure(text="服务已启动")
        self.status_detail.configure(text="前端、后端和项目内 skills 已就绪。关闭浏览器不会停止服务。")
        self.url_label.configure(text=url)

    def _show_error(self, message: str) -> None:
        self.status_chip.configure(text="启动失败", fg=COLORS["red"])
        self.status_title.configure(text="服务未启动")
        self.status_detail.configure(text=message or "未知启动错误。")
        self.url_label.configure(text="请保留 RELEASE_MANIFEST.txt 反馈问题。")

    def _open_regpilot(self) -> None:
        if self.controller.url:
            webbrowser.open(self.controller.url)

    def _stop_and_exit(self) -> None:
        self.status_chip.configure(text="正在停止", fg=COLORS["amber"])
        self.root.update_idletasks()
        self.controller.stop()
        self.root.destroy()
