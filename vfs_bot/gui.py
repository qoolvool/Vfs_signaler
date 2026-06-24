import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from .config import (
    AppConfig,
    IMAPConfig,
    ProxyConfig,
    TelegramConfig,
    VFSConfig,
    load_config,
)
from . import main as bot_main

logger = logging.getLogger(__name__)


class _QueueHandler(logging.Handler):
    """Routes log records into a queue for the GUI to consume."""

    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


class VFSBotGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("VFS Slot Monitor")
        self.root.geometry("750x820")
        self.root.minsize(620, 600)

        self.stop_event = threading.Event()
        self.bot_thread: threading.Thread | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.vars: dict[str, tk.Variable] = {}
        self.entries: list[tk.Widget] = []

        self._build_ui()
        self._load_defaults()
        self._setup_logging()
        self._poll_log()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction ----

    def _entry(self, parent, row, label, key, *, show=None, width=45):
        ttk.Label(parent, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(5, 10), pady=3,
        )
        var = tk.StringVar()
        self.vars[key] = var
        kw: dict = {"textvariable": var, "width": width}
        if show:
            kw["show"] = show
        entry = ttk.Entry(parent, **kw)
        entry.grid(row=row, column=1, sticky="ew", padx=5, pady=3)
        parent.columnconfigure(1, weight=1)
        self.entries.append(entry)
        return entry

    def _build_ui(self):
        root = self.root

        style = ttk.Style()
        style.configure("Run.TLabel", foreground="green")
        style.configure("Stop.TLabel", foreground="red")
        style.configure("Wait.TLabel", foreground="orange")

        # ---- Notebook (tabs) ----
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", padx=10, pady=(10, 0))

        # -- Tab 1: VFS --
        tab_vfs = ttk.Frame(notebook, padding=10)
        notebook.add(tab_vfs, text=" VFS ")

        self._entry(tab_vfs, 0, "Login URL:", "login_url")
        self._entry(tab_vfs, 1, "Appointment URL:", "appointment_url")
        self._entry(tab_vfs, 2, "Application Centre:", "application_centre")
        self._entry(tab_vfs, 3, "Category:", "category")
        self._entry(tab_vfs, 4, "Sub-category:", "sub_category")
        self._entry(tab_vfs, 5, "Email:", "vfs_email")
        self._entry(tab_vfs, 6, "Password:", "vfs_password", show="*")

        interval_frame = ttk.Frame(tab_vfs)
        interval_frame.grid(row=7, column=0, columnspan=2, sticky="ew", padx=5, pady=3)
        ttk.Label(interval_frame, text="Интервал (сек):  мин").pack(side="left")
        var_min = tk.StringVar()
        self.vars["poll_min"] = var_min
        e_min = ttk.Entry(interval_frame, textvariable=var_min, width=7)
        e_min.pack(side="left", padx=(5, 10))
        self.entries.append(e_min)
        ttk.Label(interval_frame, text="макс").pack(side="left")
        var_max = tk.StringVar()
        self.vars["poll_max"] = var_max
        e_max = ttk.Entry(interval_frame, textvariable=var_max, width=7)
        e_max.pack(side="left", padx=(5, 0))
        self.entries.append(e_max)

        headless_var = tk.BooleanVar()
        self.vars["headless"] = headless_var
        self.headless_cb = ttk.Checkbutton(
            tab_vfs, text="Headless (без окна браузера)", variable=headless_var,
        )
        self.headless_cb.grid(row=8, column=0, columnspan=2, sticky="w", padx=5, pady=3)

        # -- Tab 2: Connections --
        tab_conn = ttk.Frame(notebook, padding=10)
        notebook.add(tab_conn, text=" Подключения ")

        imap_frame = ttk.LabelFrame(tab_conn, text="IMAP (для OTP)", padding=8)
        imap_frame.pack(fill="x", pady=(0, 8))
        self._entry(imap_frame, 0, "Host:", "imap_host")
        self._entry(imap_frame, 1, "Port:", "imap_port", width=8)
        self._entry(imap_frame, 2, "Username:", "imap_username")
        self._entry(imap_frame, 3, "Password:", "imap_password", show="*")

        tg_frame = ttk.LabelFrame(tab_conn, text="Telegram", padding=8)
        tg_frame.pack(fill="x", pady=(0, 8))
        self._entry(tg_frame, 0, "Bot Token:", "tg_bot_token", show="*")
        self._entry(tg_frame, 1, "Chat ID:", "tg_chat_id")

        proxy_frame = ttk.LabelFrame(tab_conn, text="Proxy", padding=8)
        proxy_frame.pack(fill="x", pady=(0, 8))
        self._entry(proxy_frame, 0, "Server:", "proxy_server")
        self._entry(proxy_frame, 1, "Username:", "proxy_username")
        self._entry(proxy_frame, 2, "Password:", "proxy_password", show="*")

        # ---- Controls ----
        ctrl_frame = ttk.Frame(root)
        ctrl_frame.pack(fill="x", padx=10, pady=8)

        self.start_btn = ttk.Button(
            ctrl_frame, text="▶  Запустить",
            command=self._start_bot,
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ttk.Button(
            ctrl_frame, text="■  Остановить",
            command=self._stop_bot, state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 20))

        self.status_label = ttk.Label(
            ctrl_frame, text="● Остановлен",
            style="Stop.TLabel",
        )
        self.status_label.pack(side="left")

        clear_btn = ttk.Button(ctrl_frame, text="Clear log", command=self._clear_log)
        clear_btn.pack(side="right")

        # ---- Log ----
        log_frame = ttk.LabelFrame(root, text="Лог")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10, state="disabled", wrap="word",
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    # ---- Config loading ----

    def _load_defaults(self):
        try:
            cfg = load_config("config.yaml")
        except Exception:
            cfg = None

        if cfg:
            v = cfg.vfs
            self.vars["login_url"].set(v.login_url)
            self.vars["appointment_url"].set(v.appointment_url)
            self.vars["application_centre"].set(v.application_centre)
            self.vars["category"].set(v.category)
            self.vars["sub_category"].set(v.sub_category)
            self.vars["vfs_email"].set(v.email)
            self.vars["vfs_password"].set(v.password)
            self.vars["poll_min"].set(str(v.poll_interval_min_seconds))
            self.vars["poll_max"].set(str(v.poll_interval_max_seconds))
            self.vars["headless"].set(v.headless)

            i = cfg.imap
            self.vars["imap_host"].set(i.host)
            self.vars["imap_port"].set(str(i.port))
            self.vars["imap_username"].set(i.username)
            self.vars["imap_password"].set(i.password)

            t = cfg.telegram
            self.vars["tg_bot_token"].set(t.bot_token)
            self.vars["tg_chat_id"].set(t.chat_id)

            p = cfg.proxy
            self.vars["proxy_server"].set(p.server)
            self.vars["proxy_username"].set(p.username)
            self.vars["proxy_password"].set(p.password)
        else:
            self.vars["login_url"].set("https://visa.vfsglobal.com/srb/en/hrv/login")
            self.vars["appointment_url"].set(
                "https://visa.vfsglobal.com/srb/en/hrv/book-appointment",
            )
            self.vars["application_centre"].set("Visa Application Centre,Belgrade")
            self.vars["category"].set("C visa")
            self.vars["sub_category"].set("Tourist, Visit , Business")
            self.vars["vfs_email"].set(os.environ.get("VFS_EMAIL", ""))
            self.vars["vfs_password"].set(os.environ.get("VFS_PASSWORD", ""))
            self.vars["poll_min"].set("1800")
            self.vars["poll_max"].set("2400")
            self.vars["headless"].set(False)
            self.vars["imap_host"].set("imap.gmail.com")
            self.vars["imap_port"].set("993")
            self.vars["imap_username"].set(os.environ.get("IMAP_USERNAME", ""))
            self.vars["imap_password"].set(os.environ.get("IMAP_PASSWORD", ""))
            self.vars["tg_bot_token"].set(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
            self.vars["tg_chat_id"].set(os.environ.get("TELEGRAM_CHAT_ID", ""))
            self.vars["proxy_server"].set(os.environ.get("PROXY_SERVER", ""))
            self.vars["proxy_username"].set(os.environ.get("PROXY_USERNAME", ""))
            self.vars["proxy_password"].set(os.environ.get("PROXY_PASSWORD", ""))

    def _build_config(self) -> AppConfig:
        return AppConfig(
            vfs=VFSConfig(
                login_url=self.vars["login_url"].get(),
                appointment_url=self.vars["appointment_url"].get(),
                application_centre=self.vars["application_centre"].get(),
                category=self.vars["category"].get(),
                sub_category=self.vars["sub_category"].get(),
                email=self.vars["vfs_email"].get(),
                password=self.vars["vfs_password"].get(),
                poll_interval_min_seconds=int(self.vars["poll_min"].get() or "1800"),
                poll_interval_max_seconds=int(self.vars["poll_max"].get() or "2400"),
                headless=self.vars["headless"].get(),
            ),
            imap=IMAPConfig(
                host=self.vars["imap_host"].get() or "imap.gmail.com",
                port=int(self.vars["imap_port"].get() or "993"),
                username=self.vars["imap_username"].get(),
                password=self.vars["imap_password"].get(),
            ),
            telegram=TelegramConfig(
                bot_token=self.vars["tg_bot_token"].get(),
                chat_id=self.vars["tg_chat_id"].get(),
            ),
            proxy=ProxyConfig(
                server=self.vars["proxy_server"].get(),
                username=self.vars["proxy_username"].get(),
                password=self.vars["proxy_password"].get(),
            ),
        )

    # ---- Bot control ----

    def _set_fields_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for entry in self.entries:
            entry.config(state=state)
        self.headless_cb.config(state=state)

    def _start_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            return

        try:
            config = self._build_config()
        except (ValueError, TypeError) as exc:
            self._log_msg(f"Ошибка в настройках: {exc}")
            return

        self.stop_event.clear()
        self._set_fields_enabled(False)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="● Работает", style="Run.TLabel")

        def target():
            try:
                bot_main.run(config=config, stop_event=self.stop_event)
            except Exception:
                logger.exception("Bot crashed")
            finally:
                self.root.after(0, self._on_bot_stopped)

        self.bot_thread = threading.Thread(target=target, daemon=True)
        self.bot_thread.start()
        self._log_msg("Бот запущен")

    def _stop_bot(self):
        self.stop_event.set()
        self.stop_btn.config(state="disabled")
        self.status_label.config(
            text="● Останавливается…",
            style="Wait.TLabel",
        )
        self._log_msg("Остановка бота...")

    def _on_bot_stopped(self):
        self._set_fields_enabled(True)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="● Остановлен", style="Stop.TLabel")
        self._log_msg("Бот остановлен")

    # ---- Logging ----

    def _setup_logging(self):
        handler = _QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

    def _poll_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log_msg(msg)
        self.root.after(200, self._poll_log)

    def _log_msg(self, text: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ---- Lifecycle ----

    def _on_close(self):
        if self.bot_thread and self.bot_thread.is_alive():
            self.stop_event.set()
            self.bot_thread.join(timeout=5)
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()
