import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from .config import (
    AccountConfig,
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

        self._account_rows: list[dict] = []
        self._account_entries: list[tk.Widget] = []

        self._build_ui()
        self._load_defaults()
        self._setup_logging()
        self._poll_log()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction ----

    def _entry(self, parent, row, label, key, *, show=None, width=45):
        ttk.Label(parent, text=label, anchor="w").grid(
            row=row,
            column=0,
            sticky="w",
            padx=(5, 10),
            pady=3,
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
        self._entry(tab_vfs, 3, "Centre keyword:", "centre_keyword")
        self._entry(tab_vfs, 4, "Category:", "category")
        self._entry(tab_vfs, 5, "Category keyword:", "category_keyword")
        self._entry(tab_vfs, 6, "Sub-category:", "sub_category")
        self._entry(tab_vfs, 7, "Sub-cat keyword:", "sub_category_keyword")
        self._entry(tab_vfs, 8, "Email:", "vfs_email")
        self._entry(tab_vfs, 9, "Password:", "vfs_password", show="*")

        interval_frame = ttk.Frame(tab_vfs)
        interval_frame.grid(row=10, column=0, columnspan=2, sticky="ew", padx=5, pady=3)
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
            tab_vfs,
            text="Headless (без окна браузера)",
            variable=headless_var,
        )
        self.headless_cb.grid(
            row=11, column=0, columnspan=2, sticky="w", padx=5, pady=3
        )

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
        self._entry(tg_frame, 1, "Chat IDs (через запятую):", "tg_chat_id")

        proxy_frame = ttk.LabelFrame(tab_conn, text="Proxy", padding=8)
        proxy_frame.pack(fill="x", pady=(0, 8))
        self._entry(proxy_frame, 0, "Server:", "proxy_server")
        self._entry(proxy_frame, 1, "Username:", "proxy_username")
        self._entry(proxy_frame, 2, "Password:", "proxy_password", show="*")

        # -- Tab 3: Accounts --
        tab_acc = ttk.Frame(notebook, padding=10)
        notebook.add(tab_acc, text=" Аккаунты ")

        ttk.Label(
            tab_acc,
            text=(
                "Дополнительные аккаунты VFS. При блокировке одного бот\n"
                "переключится на следующий. Если список пуст, используются\n"
                "Email/Password со вкладки VFS."
            ),
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        scroll_frame = ttk.Frame(tab_acc)
        scroll_frame.pack(fill="both", expand=True)

        self._acc_canvas = tk.Canvas(scroll_frame, highlightthickness=0, borderwidth=0)
        acc_scrollbar = ttk.Scrollbar(
            scroll_frame, orient="vertical", command=self._acc_canvas.yview
        )
        self._acc_inner = ttk.Frame(self._acc_canvas)

        self._acc_inner.bind(
            "<Configure>",
            lambda _: self._acc_canvas.configure(
                scrollregion=self._acc_canvas.bbox("all")
            ),
        )
        self._acc_canvas_win = self._acc_canvas.create_window(
            (0, 0), window=self._acc_inner, anchor="nw"
        )
        self._acc_canvas.configure(yscrollcommand=acc_scrollbar.set)
        self._acc_canvas.bind(
            "<Configure>",
            lambda e: self._acc_canvas.itemconfig(self._acc_canvas_win, width=e.width),
        )

        self._acc_canvas.pack(side="left", fill="both", expand=True)
        acc_scrollbar.pack(side="right", fill="y")

        self._add_acc_btn = ttk.Button(
            tab_acc, text="+ Добавить аккаунт", command=self._add_account_row
        )
        self._add_acc_btn.pack(anchor="w", pady=(8, 0))

        # ---- Controls ----
        ctrl_frame = ttk.Frame(root)
        ctrl_frame.pack(fill="x", padx=10, pady=8)

        self.start_btn = ttk.Button(
            ctrl_frame,
            text="▶  Запустить",
            command=self._start_bot,
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = ttk.Button(
            ctrl_frame,
            text="■  Остановить",
            command=self._stop_bot,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 20))

        self.status_label = ttk.Label(
            ctrl_frame,
            text="● Остановлен",
            style="Stop.TLabel",
        )
        self.status_label.pack(side="left")

        clear_btn = ttk.Button(ctrl_frame, text="Clear log", command=self._clear_log)
        clear_btn.pack(side="right")

        # ---- Log ----
        log_frame = ttk.LabelFrame(root, text="Лог")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=10,
            state="disabled",
            wrap="word",
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    # ---- Account management ----

    def _add_account_row(
        self,
        label="",
        vfs_email="",
        vfs_password="",
        imap_username="",
        imap_password="",
    ):
        idx = len(self._account_rows)
        card = ttk.LabelFrame(self._acc_inner, text=f"Аккаунт {idx + 1}", padding=8)
        card.pack(fill="x", pady=(0, 5), padx=2)

        row_vars: dict[str, tk.StringVar] = {}
        row_entries: list[tk.Widget] = []

        fields = [
            ("Метка:", "label", label, None),
            ("VFS Email:", "vfs_email", vfs_email, None),
            ("VFS Password:", "vfs_password", vfs_password, "*"),
            ("IMAP Email:", "imap_username", imap_username, None),
            ("IMAP Password:", "imap_password", imap_password, "*"),
        ]

        for r, (lbl_text, key, default, show) in enumerate(fields):
            ttk.Label(card, text=lbl_text, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(5, 10), pady=2
            )
            var = tk.StringVar(value=default)
            row_vars[key] = var
            kw: dict = {"textvariable": var, "width": 35}
            if show:
                kw["show"] = show
            entry = ttk.Entry(card, **kw)
            entry.grid(row=r, column=1, sticky="ew", padx=5, pady=2)
            row_entries.append(entry)

        card.columnconfigure(1, weight=1)

        del_btn = ttk.Button(
            card, text="✕ Удалить", command=lambda c=card: self._remove_account_row(c)
        )
        del_btn.grid(row=0, column=2, padx=(10, 5), pady=2)

        row_data = {
            "frame": card,
            "vars": row_vars,
            "entries": row_entries,
            "delete_btn": del_btn,
        }
        self._account_rows.append(row_data)
        self._account_entries.extend(row_entries)

    def _remove_account_row(self, card):
        for row in self._account_rows:
            if row["frame"] is card:
                for entry in row["entries"]:
                    if entry in self._account_entries:
                        self._account_entries.remove(entry)
                self._account_rows.remove(row)
                break
        card.destroy()
        for i, row in enumerate(self._account_rows):
            row["frame"].config(text=f"Аккаунт {i + 1}")

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
            self.vars["centre_keyword"].set(v.centre_keyword)
            self.vars["category"].set(v.category)
            self.vars["category_keyword"].set(v.category_keyword)
            self.vars["sub_category"].set(v.sub_category)
            self.vars["sub_category_keyword"].set(v.sub_category_keyword)
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

            for acc in cfg.accounts:
                self._add_account_row(
                    label=acc.label,
                    vfs_email=acc.vfs_email,
                    vfs_password=acc.vfs_password,
                    imap_username=acc.imap_username,
                    imap_password=acc.imap_password,
                )
        else:
            self.vars["login_url"].set("https://visa.vfsglobal.com/srb/en/hrv/login")
            self.vars["appointment_url"].set(
                "https://visa.vfsglobal.com/srb/en/hrv/book-appointment",
            )
            self.vars["application_centre"].set("Visa Application Centre,Belgrade")
            self.vars["centre_keyword"].set("belgrade")
            self.vars["category"].set("C visa")
            self.vars["category_keyword"].set("c visa")
            self.vars["sub_category"].set("Tourist, Visit , Business")
            self.vars["sub_category_keyword"].set("tourist")
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
        accounts = []
        for row in self._account_rows:
            v = row["vars"]
            email = v["vfs_email"].get().strip()
            if not email:
                continue
            accounts.append(
                AccountConfig(
                    vfs_email=email,
                    vfs_password=v["vfs_password"].get(),
                    imap_username=v["imap_username"].get(),
                    imap_password=v["imap_password"].get(),
                    label=v["label"].get(),
                )
            )

        return AppConfig(
            vfs=VFSConfig(
                login_url=self.vars["login_url"].get(),
                appointment_url=self.vars["appointment_url"].get(),
                application_centre=self.vars["application_centre"].get(),
                centre_keyword=self.vars["centre_keyword"].get(),
                category=self.vars["category"].get(),
                category_keyword=self.vars["category_keyword"].get(),
                sub_category=self.vars["sub_category"].get(),
                sub_category_keyword=self.vars["sub_category_keyword"].get(),
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
            accounts=accounts,
        )

    # ---- Bot control ----

    def _set_fields_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for entry in self.entries:
            entry.config(state=state)
        for entry in self._account_entries:
            entry.config(state=state)
        self.headless_cb.config(state=state)
        self._add_acc_btn.config(state=state)
        for row in self._account_rows:
            row["delete_btn"].config(state=state)

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
