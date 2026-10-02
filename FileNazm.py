# FileNazm.py - نسخه نهایی با System Tray
from __future__ import annotations

import sys
import os
import re
import shutil
import time
import logging
import fnmatch
import threading
import unicodedata
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import winreg

import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from send2trash import send2trash
import pystray
from PIL import Image, ImageDraw

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    import docx
except Exception:
    docx = None


def resource_path(relative_path):
    """پیدا کردن مسیر فایل‌های همراه با exe (PyInstaller) یا در حالت توسعه"""
    try:
        # PyInstaller پوشه موقت برای فایل‌های همراه می‌سازد
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


if os.name == 'nt':
    APP_DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
else:
    APP_DATA_DIR = Path.home() / '.config'

LEGACY_APP_NAME = "Saman"
APP_NAME = "FileNazm"
APP_DISPLAY_NAME = "فایل‌نظم"
APP_ICON_FILE = "FileNazm.ico"

LEGACY_APP_DIR = APP_DATA_DIR / LEGACY_APP_NAME
APP_DIR = APP_DATA_DIR / APP_NAME


def _migrate_legacy_app_dir():
    """
    تنظیمات نسخه قبلی (Saman) را بدون حذف، به پوشه جدید (FileNazm) کپی می‌کند.
    اگر فایلی از قبل در پوشه جدید باشد، بازنویسی نمی‌شود.
    """
    try:
        if not LEGACY_APP_DIR.exists() or not LEGACY_APP_DIR.is_dir():
            return

        APP_DIR.mkdir(parents=True, exist_ok=True)

        for item in LEGACY_APP_DIR.rglob("*"):
            try:
                rel = item.relative_to(LEGACY_APP_DIR)
                target = APP_DIR / rel

                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item), str(target))
            except Exception:
                pass
    except Exception:
        pass


def _cleanup_legacy_startup():
    """
    مقدار قدیمی Registry با نام Saman را پاک می‌کند تا Startup شکسته نماند.
    فقط HKEY_CURRENT_USER؛ چون برنامه برای کاربر اجرا می‌شود.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, LEGACY_APP_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


_migrate_legacy_app_dir()
APP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = APP_DIR / "rules.yaml"
LOG_FILE = APP_DIR / "filenazm.log"

IS_AUTOSTART = "--autostart" in sys.argv

SETTINGS: Dict[str, Any] = {}
RECENT_IGNORED: Dict[str, float] = {}
RECENT_LOCK = threading.Lock()
LOG_TEXT_WIDGET = None
TRAY_ICON = None
MAIN_APP = None

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm", ".ini", ".yaml", ".yml"}
IGNORE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
IGNORE_SUFFIXES = {".tmp", ".temp", ".part", ".crdownload", ".download", ".partial"}


def get_icon_image():
    """آیکون را از فایل FileNazm.ico می‌خواند یا یکی می‌سازد"""
    candidates = [
        Path(resource_path(APP_ICON_FILE)),
        Path(os.path.dirname(sys.executable)) / APP_ICON_FILE,
    ]

    try:
        candidates.append(Path(os.path.dirname(os.path.abspath(__file__))) / APP_ICON_FILE)
    except NameError:
        pass

    for icon_path in candidates:
        try:
            if icon_path.exists():
                return Image.open(str(icon_path))
        except Exception as exc:
            logging.warning("Could not load icon from %s: %s", icon_path, exc)

    return create_fallback_icon()


def create_fallback_icon():
    """آیکون پیش‌فرض ساده را می‌سازد"""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size-2, size-2], fill=(52, 152, 219))
    draw.rectangle([15, 25, 49, 45], fill=(241, 196, 15))
    draw.rectangle([20, 20, 35, 25], fill=(243, 156, 18))
    draw.ellipse([40, 15, 55, 30], fill=(255, 255, 255))
    draw.ellipse([44, 19, 51, 26], fill=(52, 152, 219))
    return img


class GUILogHandler(logging.Handler):
    def emit(self, record):
        global LOG_TEXT_WIDGET
        if LOG_TEXT_WIDGET:
            msg = self.format(record)
            def append():
                if LOG_TEXT_WIDGET and LOG_TEXT_WIDGET.winfo_exists():
                    LOG_TEXT_WIDGET.configure(state='normal')
                    LOG_TEXT_WIDGET.insert(tk.END, msg + '\n')
                    LOG_TEXT_WIDGET.see(tk.END)
                    LOG_TEXT_WIDGET.configure(state='disabled')
            try:
                LOG_TEXT_WIDGET.after(0, append)
            except:
                pass


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def slugify(text: Any) -> str:
    text = str(text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)
    return text.strip("-").lower() or "rule"


def normalize_extension(ext: str) -> str:
    ext = str(ext).strip().lower()
    return ext if ext.startswith(".") else "." + ext


def parse_extensions(ext_string: str) -> List[str]:
    if not ext_string or not ext_string.strip():
        return []
    ext_string = ext_string.replace("،", ",").replace("٬", ",")
    return [normalize_extension(e.strip()) for e in ext_string.split(",") if e.strip()]


def is_ignored_path(path: Path) -> bool:
    try:
        if not path.is_file():
            return True
    except Exception:
        return True
    name = path.name.lower()
    if name in IGNORE_NAMES or name.startswith("~$"):
        return True
    if any(name.endswith(suffix) for suffix in IGNORE_SUFFIXES):
        return True
    if SETTINGS.get("ignore_hidden", True) and name.startswith("."):
        return True
    return False


def read_text_file(path: Path, max_mb: float) -> str:
    try:
        if path.stat().st_size > max_mb * 1024 * 1024:
            return ""
        return path.read_text(errors="ignore")
    except Exception:
        return ""


def read_pdf_file(path: Path, max_mb: float) -> str:
    if PdfReader is None:
        return ""
    try:
        if path.stat().st_size > max_mb * 1024 * 1024:
            return ""
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:int(SETTINGS.get("max_pdf_pages", 200))])
    except Exception:
        return ""


def read_docx_file(path: Path, max_mb: float) -> str:
    if docx is None:
        return ""
    try:
        if path.stat().st_size > max_mb * 1024 * 1024:
            return ""
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    except Exception:
        return ""


def extract_content(path: Path) -> Optional[str]:
    max_mb = float(SETTINGS.get("max_content_scan_mb", 20))
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_EXTENSIONS:
            return read_text_file(path, max_mb)
        if suffix == ".pdf":
            return read_pdf_file(path, max_mb)
        if suffix == ".docx":
            return read_docx_file(path, max_mb)
    except Exception:
        pass
    return None


def matches_rule(rule: Dict[str, Any], path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        conditions = rule.get("conditions", {}) or {}
        exts = conditions.get("extensions")
        if exts and path.suffix.lower() not in {normalize_extension(x) for x in exts}:
            return False
        globs = conditions.get("filename_globs")
        if globs and not any(fnmatch.fnmatch(path.name.lower(), str(p).lower()) for p in globs):
            return False
        stat = path.stat()
        min_mb = conditions.get("min_size_mb")
        max_mb = conditions.get("max_size_mb")
        if min_mb is not None and stat.st_size < min_mb * 1024 * 1024:
            return False
        if max_mb is not None and stat.st_size > max_mb * 1024 * 1024:
            return False
        content_contains = conditions.get("content_contains")
        if content_contains:
            content = extract_content(path)
            if content is None:
                return False
            terms = [str(t).lower() for t in (content_contains if isinstance(content_contains, list) else [content_contains])]
            if not all(t in content.lower() for t in terms):
                return False
        return True
    except Exception:
        return False


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.parent / f"{path.stem}_{counter}{path.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_destination(src: Path, dest_template: str, rule_name: str) -> Path:
    if not dest_template:
        return src
    try:
        now = datetime.fromtimestamp(src.stat().st_mtime)
    except (OSError, ValueError, OverflowError):
        now = datetime.now()
    ext = src.suffix.lstrip(".").lower()
    context = {
        "filename": src.stem,
        "name": src.stem,
        "ext": ext,
        "date": now.strftime("%Y-%m-%d"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "rule": slugify(rule_name)
    }
    try:
        rendered = str(dest_template).format_map(SafeDict(context))
    except Exception:
        rendered = dest_template
    dest = Path(rendered).expanduser()
    if not dest.is_absolute():
        dest = Path.cwd() / dest
    if dest.is_dir() or str(dest_template).rstrip().endswith(("/", "\\")) or not dest.suffix:
        return dest / src.name
    return dest


def execute_actions(rule: Dict[str, Any], path: Path) -> Optional[Path]:
    actions = rule.get("actions", []) or []
    current = path
    for action in actions:
        if not current.exists():
            break
        action_type = str(action.get("type", "")).strip().lower()
        try:
            if action_type in {"move", "copy"}:
                dest = resolve_destination(current, str(action.get("destination", "")), str(rule.get("name", "rule")))
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and not action.get("overwrite", False):
                    dest = ensure_unique_path(dest)
                if action_type == "move":
                    shutil.move(str(current), str(dest))
                    logging.info("MOVED: %s -> %s", current, dest)
                    current = dest
                else:
                    shutil.copy2(str(current), str(dest))
                    logging.info("COPIED: %s -> %s", current, dest)
            elif action_type == "delete":
                if action.get("recycle", True):
                    send2trash(str(current))
                    logging.info("TRASHED: %s", current)
                else:
                    current.unlink()
                    logging.info("DELETED: %s", current)
                return None
        except Exception as e:
            logging.error("Action failed: %s on %s | Error: %s", action_type, current, e)
    return current


class RuleHandler(FileSystemEventHandler):
    def __init__(self, folder: Path, rules: List[Dict[str, Any]]):
        self.folder = folder
        self.rules = rules
        self.timers: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()

    def schedule(self, path_str: str) -> None:
        path = Path(path_str).expanduser()
        if not path.exists() or is_ignored_path(path):
            return
        key = str(path.absolute())
        with self.lock:
            if key in self.timers:
                self.timers[key].cancel()
            timer = threading.Timer(float(SETTINGS.get("debounce_seconds", 2)), self.process, args=[key])
            timer.daemon = True
            self.timers[key] = timer
            timer.start()

    def on_created(self, event):
        if not event.is_directory:
            self.schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.schedule(event.dest_path)

    def on_modified(self, event):
        if SETTINGS.get("react_to_modified", True) and not event.is_directory:
            self.schedule(event.src_path)

    def process(self, path_str: str) -> None:
        with self.lock:
            self.timers.pop(path_str, None)
        path = Path(path_str)
        if not path.exists() or is_ignored_path(path):
            return
        try:
            size1 = path.stat().st_size
            time.sleep(float(SETTINGS.get("stable_interval", 0.5)))
            if size1 != path.stat().st_size:
                self.schedule(path_str)
                return
        except Exception:
            return
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            try:
                if matches_rule(rule, path):
                    logging.info("Rule matched: '%s' | File: %s", rule.get("name"), path)
                    new_path = execute_actions(rule, path)
                    if new_path is None or new_path != path:
                        break
            except Exception as e:
                logging.error("Rule failed: %s | Error: %s", rule.get("name"), e)


class RuleDialog(tk.Toplevel):
    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.title("افزودن قانون جدید" if rule is None else "ویرایش قانون")
        self.geometry("600x500")
        self.result = None
        self.rule = rule or {}
        main_frame = ttk.Frame(self, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(main_frame, text="نام قانون:").grid(row=0, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar(value=self.rule.get("name", ""))
        ttk.Entry(main_frame, textvariable=self.name_var, width=40).grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(main_frame, text="پوشه نظارت:").grid(row=1, column=0, sticky="w", pady=5)
        self.watch_var = tk.StringVar(value=self.rule.get("watch", ""))
        ttk.Entry(main_frame, textvariable=self.watch_var, width=40).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(main_frame, text="...", command=lambda: self.watch_var.set(filedialog.askdirectory().replace("/", "\\"))).grid(row=1, column=2, padx=5)
        ttk.Label(main_frame, text="پسوندها (با کاما جدا کن):").grid(row=2, column=0, sticky="w", pady=5)
        exts = self.rule.get("conditions", {}).get("extensions", [])
        self.ext_var = tk.StringVar(value=", ".join(exts) if exts else "")
        ttk.Entry(main_frame, textvariable=self.ext_var, width=40).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(main_frame, text="نوع عملیات:").grid(row=3, column=0, sticky="w", pady=5)
        self.action_type_var = tk.StringVar(value="move")
        actions = self.rule.get("actions", [])
        if actions:
            self.action_type_var.set(actions[0].get("type", "move"))
        ttk.Combobox(main_frame, textvariable=self.action_type_var, values=["move", "copy", "delete"], state="readonly").grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(main_frame, text="پوشه مقصد:").grid(row=4, column=0, sticky="w", pady=5)
        dest = actions[0].get("destination", "") if actions else ""
        self.dest_var = tk.StringVar(value=dest)
        ttk.Entry(main_frame, textvariable=self.dest_var, width=40).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(main_frame, text="...", command=lambda: self.dest_var.set(filedialog.askdirectory().replace("/", "\\"))).grid(row=4, column=2, padx=5)
        ttk.Label(main_frame, text="راهنما: {ext}, {name}, {date}, {year}, {month}", foreground="gray", font=("Segoe UI", 8)).grid(row=5, column=0, columnspan=3, sticky="w", pady=10)
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=10)
        ttk.Button(btn_frame, text="ذخیره", command=self.save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="انصراف", command=self.destroy).pack(side=tk.LEFT, padx=5)
        main_frame.columnconfigure(1, weight=1)

    def save(self):
        name = self.name_var.get().strip()
        watch = self.watch_var.get().strip()
        if not name or not watch:
            messagebox.showerror("خطا", "نام و پوشه نظارت الزامی است", parent=self)
            return
        extensions = parse_extensions(self.ext_var.get())
        conditions = {"extensions": extensions} if extensions else {}
        action_type = self.action_type_var.get()
        if action_type == "delete":
            actions = [{"type": "delete", "recycle": True}]
        else:
            dest = self.dest_var.get().strip()
            if not dest:
                messagebox.showerror("خطا", "پوشه مقصد الزامی است", parent=self)
                return
            actions = [{"type": action_type, "destination": dest.replace("\\", "/")}]
        self.result = {
            "name": name,
            "enabled": True,
            "watch": watch.replace("\\", "/"),
            "conditions": conditions,
            "actions": actions
        }
        self.destroy()


class FileOrganizerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_DISPLAY_NAME} - سازماندهی خودکار فایل‌ها")
        self.root.geometry("900x650")
        self.root.minsize(800, 550)
        self.observer = None
        self.is_running = False
        self.rules_data = {"settings": {}, "rules": []}
        self.startup_var = tk.BooleanVar(value=False)
        global MAIN_APP
        MAIN_APP = self
        self.setup_ui()
        self.check_startup_status()
        self.load_config()
        self.setup_logging()
        if IS_AUTOSTART:
            self.root.withdraw()
            self.root.after(2000, self.auto_start_with_tray)
        else:
            self.setup_tray_icon()

    def auto_start_with_tray(self):
        self.setup_tray_icon()
        self.log_message("🚀 برنامه از طریق Startup اجرا شد - آیکون در کنار ساعت")
        self.start_monitoring(silent=True)

    def setup_tray_icon(self):
        global TRAY_ICON
        # آیکون را از فایل FileNazm.ico می‌خوانیم
        icon_image = get_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("نمایش پنجره اصلی", self.show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("شروع نظارت", self.tray_start_monitoring),
            pystray.MenuItem("توقف نظارت", self.tray_stop_monitoring),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("خروج", self.quit_app)
        )
        TRAY_ICON = pystray.Icon(APP_NAME, icon_image, APP_DISPLAY_NAME, menu)
        tray_thread = threading.Thread(target=TRAY_ICON.run, daemon=True)
        tray_thread.start()

    def show_window(self, icon=None, item=None):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def tray_start_monitoring(self, icon=None, item=None):
        self.root.after(0, self.start_monitoring)

    def tray_stop_monitoring(self, icon=None, item=None):
        self.root.after(0, self.stop_monitoring)

    def quit_app(self, icon=None, item=None):
        if TRAY_ICON:
            TRAY_ICON.stop()
        self.stop_monitoring()
        self.root.after(0, self.root.destroy)

    def setup_ui(self):
        toolbar = ttk.Frame(self.root, padding="5")
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="➕ افزودن قانون", command=self.add_rule).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="✏️ ویرایش", command=self.edit_rule).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="🗑️ حذف", command=self.delete_rule).pack(side=tk.LEFT, padx=5)
        self.startup_cb = ttk.Checkbutton(toolbar, text=" شروع خودکار با ویندوز", variable=self.startup_var, command=self.toggle_startup)
        self.startup_cb.pack(side=tk.LEFT, padx=20)
        self.start_btn = ttk.Button(toolbar, text="▶️ شروع نظارت", command=self.toggle_monitoring)
        self.start_btn.pack(side=tk.RIGHT, padx=5)
        list_frame = ttk.LabelFrame(self.root, text="قوانین", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree = ttk.Treeview(list_frame, columns=("name", "watch", "conditions", "action"), show="headings", height=10)
        self.tree.heading("name", text="نام قانون")
        self.tree.heading("watch", text="پوشه نظارت")
        self.tree.heading("conditions", text="شرایط")
        self.tree.heading("action", text="عملیات")
        self.tree.column("name", width=150)
        self.tree.column("watch", width=200)
        self.tree.column("conditions", width=200)
        self.tree.column("action", width=150)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", lambda e: self.edit_rule())
        log_frame = ttk.LabelFrame(self.root, text="گزارش عملیات", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        global LOG_TEXT_WIDGET
        LOG_TEXT_WIDGET = scrolledtext.ScrolledText(log_frame, height=8, state='disabled', font=("Consolas", 9), wrap=tk.WORD)
        LOG_TEXT_WIDGET.pack(fill=tk.BOTH, expand=True)
        self.status_var = tk.StringVar(value=f"آماده | مسیر تنظیمات: {CONFIG_FILE}")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def check_startup_status(self):
        _cleanup_legacy_startup()
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            self.startup_var.set(True)
        except FileNotFoundError:
            self.startup_var.set(False)
        except Exception:
            pass

    def toggle_startup(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if self.startup_var.get():
                exe_path = sys.executable
                if " " in exe_path:
                    exe_path = f'"{exe_path}"'
                startup_command = f'{exe_path} --autostart'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command)
                self.log_message("✅ برنامه برای شروع خودکار با ویندوز تنظیم شد.")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    self.log_message("⏹️ شروع خودکار با ویندوز غیرفعال شد.")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            self.log_message(f"❌ خطا در تغییر تنظیمات شروع خودکار: {e}")

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[GUILogHandler(), logging.FileHandler(str(LOG_FILE), encoding="utf-8")]
        )

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.rules_data = yaml.safe_load(f) or {"settings": {}, "rules": []}
                global SETTINGS
                SETTINGS = self.rules_data.get("settings", {}) or {}
                self.refresh_tree()
                self.log_message("فایل تنظیمات بارگذاری شد")
            except Exception as e:
                self.log_message(f"خطا در خواندن تنظیمات: {e}")
        else:
            self.rules_data = {"settings": {"debounce_seconds": 2, "ignore_hidden": True}, "rules": []}
            self.save_config()
            self.log_message("فایل تنظیمات جدید در AppData ساخته شد")

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(self.rules_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            self.log_message(f"خطا در ذخیره تنظیمات: {e}")

    def log_message(self, msg):
        if LOG_TEXT_WIDGET and LOG_TEXT_WIDGET.winfo_exists():
            LOG_TEXT_WIDGET.configure(state='normal')
            LOG_TEXT_WIDGET.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} | {msg}\n")
            LOG_TEXT_WIDGET.see(tk.END)
            LOG_TEXT_WIDGET.configure(state='disabled')

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rule in self.rules_data.get("rules", []):
            conds = rule.get("conditions", {})
            exts = conds.get("extensions", [])
            conds_str = ", ".join(exts) if exts else "همه فایل‌ها"
            actions = rule.get("actions", [])
            action_str = actions[0].get("type", "") if actions else ""
            self.tree.insert("", tk.END, values=(rule.get("name", ""), rule.get("watch", ""), conds_str, action_str))

    def add_rule(self):
        dialog = RuleDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result:
            self.rules_data["rules"].append(dialog.result)
            self.save_config()
            self.refresh_tree()
            self.log_message(f"قانون '{dialog.result['name']}' اضافه شد")

    def edit_rule(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("هشدار", "یک قانون را انتخاب کن")
        idx = self.tree.index(sel[0])
        if idx < len(self.rules_data.get("rules", [])):
            dialog = RuleDialog(self.root, self.rules_data["rules"][idx])
            self.root.wait_window(dialog)
            if dialog.result:
                self.rules_data["rules"][idx] = dialog.result
                self.save_config()
                self.refresh_tree()
                self.log_message(f"قانون '{dialog.result['name']}' ویرایش شد")

    def delete_rule(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("هشدار", "یک قانون را انتخاب کن")
        if messagebox.askyesno("تأیید", "آیا مطمئنی؟"):
            idx = self.tree.index(sel[0])
            deleted = self.rules_data["rules"].pop(idx)
            self.save_config()
            self.refresh_tree()
            self.log_message(f"قانون '{deleted.get('name', '')}' حذف شد")

    def toggle_monitoring(self):
        self.stop_monitoring() if self.is_running else self.start_monitoring()

    def start_monitoring(self, silent=False):
        enabled_rules = [r for r in self.rules_data.get("rules", []) if r.get("enabled", True)]
        if not enabled_rules:
            if not silent:
                messagebox.showwarning("هشدار", "هیچ قانون فعالی وجود ندارد")
            else:
                self.log_message("⚠️ هیچ قانون فعالی برای شروع خودکار وجود ندارد")
            return
        try:
            self.observer = Observer()
            watch_map = {}
            for rule in enabled_rules:
                folder = Path(rule.get("watch")).expanduser().resolve()
                if folder.is_dir():
                    watch_map.setdefault(folder, []).append(rule)
                else:
                    self.log_message(f"پوشه یافت نشد: {folder}")
            if not watch_map:
                if not silent:
                    messagebox.showerror("خطا", "هیچ پوشه معتبری پیدا نشد")
                return
            for folder, rules in watch_map.items():
                self.observer.schedule(RuleHandler(folder, rules), str(folder), recursive=bool(SETTINGS.get("recursive", False)))
                self.log_message(f"نظارت شروع شد: {folder}")
            self.observer.start()
            self.is_running = True
            self.start_btn.config(text="⏹️ توقف نظارت")
            self.status_var.set(f"در حال نظارت روی {len(watch_map)} پوشه...")
            self.log_message("✅ نظارت شروع شد")
            if TRAY_ICON:
                TRAY_ICON.title = f"{APP_DISPLAY_NAME} - در حال نظارت"
        except Exception as e:
            self.log_message(f"خطا در شروع: {e}")
            self.observer = None

    def stop_monitoring(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.is_running = False
            self.start_btn.config(text="▶️ شروع نظارت")
            self.status_var.set("متوقف شد")
            self.log_message("⏹️ نظارت متوقف شد")
            if TRAY_ICON:
                TRAY_ICON.title = f"{APP_DISPLAY_NAME} - متوقف"

    def run(self):
        self.root.mainloop()

    def on_closing(self):
        if TRAY_ICON:
            self.root.withdraw()
            self.log_message("📌 برنامه در کنار ساعت فعال است")
        else:
            if self.is_running and not messagebox.askyesno("خروج", "نظارت در حال اجراست. خارج شوی؟"):
                return
            self.stop_monitoring()
            self.root.destroy()


if __name__ == "__main__":
    FileOrganizerApp().run()