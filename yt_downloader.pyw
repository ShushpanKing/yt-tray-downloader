"""Простой загрузчик видео с YouTube: ссылка -> проверить -> выбрать качество -> скачать.

Живёт в трее: крестик прячет окно, выход через меню иконки.
Запуск с флагом --tray стартует сразу в трее (для автозапуска).
"""

import ctypes
import json
import os
import socket
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pystray
import yt_dlp
from PIL import Image

APP_NAME = "YouTube загрузчик"
AUDIO_LABEL = "Только звук (mp3)"
HERE = Path(__file__).parent
# порт, по которому вторая копия программы просит первую показать окно
INSTANCE_PORT = 47815
SETTINGS_FILE = Path(os.environ.get("APPDATA", HERE)) / "yt_downloader" / "settings.json"

# Виртуальные коды клавиш Windows: не зависят от раскладки, в отличие от keysym
VK_A, VK_C, VK_V, VK_X = 65, 67, 86, 88


class QuietLogger:
    """Глушит вывод yt-dlp: у .pyw нет консоли, и запись в stdout может уронить программу."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def base_opts():
    return {
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": QuietLogger(),
    }


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings), encoding="utf-8")
    except OSError:
        pass


def claim_single_instance():
    """Возвращает слушающий сокет, если мы первая копия; иначе будит первую копию и возвращает None."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", INSTANCE_PORT))
        server.listen()
        return server
    except OSError:
        server.close()
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as s:
                s.sendall(b"show")
        except OSError:
            pass
        return None


class App:
    def __init__(self, root, server, start_hidden):
        self.root = root
        self.heights = {}  # подпись в списке -> высота кадра
        self.all_heights = []  # все высоты у проверенного видео
        self.h264_heights = []  # высоты, которые есть в H.264 (YouTube отдаёт его максимум до 1080p)
        self.checked_url = None
        self.video_title = ""
        self.settings = load_settings()

        root.title(APP_NAME)
        root.resizable(False, False)
        root.iconbitmap(HERE / "icon.ico")
        root.protocol("WM_DELETE_WINDOW", self.hide)

        frame = ttk.Frame(root, padding=12)
        frame.grid()

        ttk.Label(frame, text="Ссылка на видео:").grid(row=0, column=0, columnspan=2, sticky="w")
        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", lambda *_: self.reset_formats())
        url_entry = ttk.Entry(frame, textvariable=self.url_var, width=60)
        url_entry.grid(row=1, column=0, sticky="we", pady=(2, 8))
        url_entry.bind("<Control-KeyPress>", self.on_ctrl_key)
        url_entry.focus()
        self.check_btn = ttk.Button(frame, text="Проверить", command=self.check)
        self.check_btn.grid(row=1, column=1, padx=(6, 0), pady=(2, 8))

        self.title_var = tk.StringVar(value="Вставь ссылку и нажми «Проверить»")
        ttk.Label(frame, textvariable=self.title_var, wraplength=480).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(frame, text="Качество:").grid(row=3, column=0, columnspan=2, sticky="w")
        self.quality = ttk.Combobox(frame, state="disabled", width=30)
        self.quality.grid(row=4, column=0, sticky="w", pady=(2, 8))
        self.download_btn = ttk.Button(frame, text="Скачать", command=self.download, state="disabled")
        self.download_btn.grid(row=4, column=1, padx=(6, 0), pady=(2, 8))

        # Premiere и многие редакторы не открывают AV1/VP9, которые YouTube отдаёт по умолчанию
        self.edit_mode = tk.BooleanVar(value=self.settings.get("edit_mode", False))
        ttk.Checkbutton(
            frame, text="Для монтажа (H.264, до 1080p)", variable=self.edit_mode, command=self.on_edit_mode
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.progress = ttk.Progressbar(frame, length=480, maximum=100)
        self.progress.grid(row=6, column=0, columnspan=2, sticky="we")
        self.status_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.status_var).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.tray = pystray.Icon(
            "yt_downloader",
            Image.open(HERE / "icon.png"),
            APP_NAME,
            pystray.Menu(
                pystray.MenuItem("Открыть", lambda: self.call_in_ui(self.show), default=True),
                pystray.MenuItem("Выход", lambda: self.call_in_ui(self.quit)),
            ),
        )
        threading.Thread(target=self.tray.run, daemon=True).start()
        threading.Thread(target=self.listen_for_instances, args=(server,), daemon=True).start()

        if start_hidden:
            root.withdraw()

    def call_in_ui(self, fn, *args):
        """Выполнить fn в главном потоке Tk. Фоновые потоки трогают окно только так."""
        self.root.after(0, fn, *args)

    # --- окно и трей ---

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def quit(self):
        self.tray.stop()
        self.root.destroy()

    def listen_for_instances(self, server):
        while True:
            conn, _ = server.accept()
            conn.close()
            self.call_in_ui(self.show)

    def on_ctrl_key(self, event):
        # Tk на Windows узнаёт Ctrl+V по букве, поэтому в русской раскладке хоткеи молчат.
        # Смотрим на физическую клавишу.
        entry = event.widget
        if event.keycode == VK_V:
            entry.event_generate("<<Paste>>")
        elif event.keycode == VK_C:
            entry.event_generate("<<Copy>>")
        elif event.keycode == VK_X:
            entry.event_generate("<<Cut>>")
        elif event.keycode == VK_A:
            entry.select_range(0, "end")
            entry.icursor("end")
        else:
            return None
        return "break"

    # --- состояние окна ---

    def reset_formats(self):
        if self.checked_url is None:
            return
        self.checked_url = None
        self.heights = {}
        self.quality.set("")
        self.quality.configure(values=[], state="disabled")
        self.download_btn.configure(state="disabled")
        self.title_var.set("Вставь ссылку и нажми «Проверить»")

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.check_btn.configure(state=state)
        self.download_btn.configure(state="disabled" if busy or not self.checked_url else "normal")
        self.quality.configure(state="disabled" if busy or not self.checked_url else "readonly")

    def show_error(self, text):
        self.status_var.set("Ошибка")
        self.set_busy(False)
        messagebox.showerror("Ошибка", text)

    def set_progress(self, percent, text):
        self.progress.configure(value=percent)
        self.status_var.set(text)

    # --- проверка ссылки ---

    def check(self):
        url = self.url_var.get().strip()
        if not url:
            return
        self.set_busy(True)
        self.status_var.set("Получаю информацию о видео…")
        self.progress.configure(value=0)
        threading.Thread(target=self.check_worker, args=(url,), daemon=True).start()

    def check_worker(self, url):
        try:
            with yt_dlp.YoutubeDL(base_opts()) as ydl:
                info = ydl.extract_info(url, download=False)
            video = [f for f in info.get("formats", []) if f.get("vcodec") not in (None, "none") and f.get("height")]
            heights = sorted({f["height"] for f in video}, reverse=True)
            h264_heights = sorted({f["height"] for f in video if f["vcodec"].startswith("avc1")}, reverse=True)
            self.call_in_ui(self.on_checked, url, info.get("title", "?"), heights, h264_heights)
        except Exception as e:
            self.call_in_ui(self.show_error, f"Не удалось получить видео:\n{e}")

    def on_checked(self, url, title, heights, h264_heights):
        self.all_heights = heights
        self.h264_heights = h264_heights
        self.title_var.set(title)
        self.video_title = title
        self.checked_url = url
        self.fill_quality_list()
        self.status_var.set("")
        self.set_busy(False)
        # trace на url_var сбросит checked_url, если ссылку поменяют

    def fill_quality_list(self):
        heights = self.h264_heights if self.edit_mode.get() else self.all_heights
        self.heights = {f"{h}p": h for h in heights}
        values = list(self.heights) + [AUDIO_LABEL]
        previous = self.quality.get()
        self.quality.configure(values=values)
        self.quality.set(previous if previous in values else values[0])

    def on_edit_mode(self):
        self.settings["edit_mode"] = self.edit_mode.get()
        save_settings(self.settings)
        if self.checked_url:
            self.fill_quality_list()

    # --- скачивание ---

    def download(self):
        folder = filedialog.askdirectory(title="Куда сохранить?")
        if not folder:
            return
        choice = self.quality.get()
        opts = base_opts()
        opts.update(
            {
                "outtmpl": f"{folder}/%(title)s.%(ext)s",
                "windowsfilenames": True,
                "progress_hooks": [self.progress_hook],
                "postprocessor_hooks": [self.postprocessor_hook],
            }
        )
        if choice == AUDIO_LABEL:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        elif self.edit_mode.get():
            h = self.heights[choice]
            opts["format"] = (
                f"bestvideo[height={h}][vcodec^=avc1]+bestaudio[ext=m4a]"
                f"/best[height<={h}][vcodec^=avc1]"
            )
            opts["merge_output_format"] = "mp4"
        else:
            h = self.heights[choice]
            # сначала пробуем mp4+m4a (лучше всего открывается в Windows), потом что угодно с этой высотой
            opts["format"] = (
                f"bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height={h}]+bestaudio"
                f"/best[height<={h}]"
            )
            opts["merge_output_format"] = "mp4"

        self.set_busy(True)
        self.progress.configure(value=0)
        self.status_var.set("Начинаю скачивание…")
        threading.Thread(
            target=self.download_worker, args=(self.checked_url, opts, folder, self.video_title), daemon=True
        ).start()

    def download_worker(self, url, opts, folder, title):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            self.call_in_ui(self.on_done, folder, title)
        except Exception as e:
            self.call_in_ui(self.show_error, f"Ошибка при скачивании:\n{e}")

    def on_done(self, folder, title):
        self.status_var.set(f"Готово! Сохранено в {folder}")
        self.set_busy(False)
        self.tray.notify(f"{title}\nСохранено в {folder}", "Скачивание завершено")

    def progress_hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            percent = done * 100 / total if total else 0
            self.call_in_ui(self.set_progress, percent, f"Скачиваю… {percent:.0f}%  ({done / 1048576:.1f} МБ)")
        elif d["status"] == "finished":
            self.call_in_ui(self.set_progress, 100, "Файл скачан, обрабатываю…")

    def postprocessor_hook(self, d):
        if d["status"] == "started":
            self.call_in_ui(self.set_progress, 100, "Склеиваю / конвертирую через ffmpeg…")


if __name__ == "__main__":
    server = claim_single_instance()
    if server is None:
        sys.exit()
    # своя иконка на панели задач вместо значка Python
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("andys.yt_downloader")
    root = tk.Tk()
    App(root, server, start_hidden="--tray" in sys.argv)
    root.mainloop()
