"""Main interface, themes, history and Windows tray; compatible with Python 3.8."""
from copy import deepcopy
from datetime import datetime
import os
import queue
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont
import config_yaml as yaml
from animation import LIGHT, DARK
from editor import RuleEditor
from flow import Flow, ScrollFrame, wrap_to_width
from engine import DEFAULT, Organizer, config_path, load_config, save_config, undo_operation, clear_undo_store, discard_backup
from model import ACTIONS, VERSION, History, validate
from preview import PreviewDialog
from resources import register_fonts, resource


def demo_rules():
    return [dict(name="مرتب‌سازی تصاویر", enabled=True, watch="C:/Demo/Downloads/", conditions={"extensions": [".jpg", ".png"]}, actions=[dict(type="move", destination="C:/Demo/Pictures/")]),
            dict(name="کپی اسناد", enabled=True, watch="C:/Demo/Downloads/", conditions={"extensions": [".pdf", ".docx"]}, actions=[dict(type="copy", destination="C:/Demo/Documents/{year}/")]),
            dict(name="پاک‌سازی گزارش‌ها", enabled=True, watch="C:/Demo/Logs/", conditions={"extensions": [".log"]}, actions=[dict(type="delete", recycle=True)])]


class App:
    def __init__(self, demo=False, autostart=False, tray=True):
        register_fonts()
        self.root = tk.Tk()
        self.set_icon()
        self.root.title("فایل‌نظم " + VERSION + (" — آزمایشی" if demo else ""))
        self.root.geometry("{}x{}".format(min(1120, self.root.winfo_screenwidth() - 60), min(780, self.root.winfo_screenheight() - 80)))
        self.root.minsize(440, 420)
        self.demo, self.path, self.engine, self.tray = demo, config_path(), None, None
        self.events, self.history, self.closing, self.config_error = queue.Queue(), History(), False, None
        self.poll_job, self.file_ops, self.order = None, [], []
        try:
            self.data = deepcopy(DEFAULT) if demo else load_config(self.path)
        except Exception as exc:
            self.data, self.config_error = deepcopy(DEFAULT), str(exc)
        if demo:
            self.data["rules"] = demo_rules()
        self.theme = "dark" if self.data["settings"].get("theme") == "dark" else "light"
        try:
            self.font_size = max(9, min(18, int(self.data["settings"].get("font_size", 11))))
        except (ValueError, TypeError):
            self.font_size = 11
        self.body_font = tkfont.Font(root=self.root, family="Vazirmatn", size=self.font_size)
        self.title_font = tkfont.Font(root=self.root, family="Vazirmatn", size=self.font_size + 8, weight="bold")
        self.heading_font = tkfont.Font(root=self.root, family="Vazirmatn", size=self.font_size + 1, weight="bold")
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.apply_style()
        self.build_ui()
        self.refresh()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind_all("<Control-KeyPress>", self.history_shortcut)
        if not self.demo:
            clear_undo_store()
        self.poll()
        if self.config_error:
            self.root.after(100, lambda: messagebox.showerror("خطای تنظیمات", self.config_error + "\nبرای حفظ فایل قبلی، ذخیره غیرفعال است. فایل تنظیمات را اصلاح و برنامه را دوباره اجرا کنید.", parent=self.root))
        if tray and not demo:
            self.setup_tray()
        if autostart and not demo and not self.config_error:
            if self.tray:
                self.root.withdraw()
            self.root.after(1000, self.start)

    def set_icon(self):
        # default= applies the icon to the main window and every dialog (editor, preview, ...).
        try:
            if os.name == "nt":
                self.root.iconbitmap(default=str(resource("FileNazm.ico")))
        except Exception:
            pass

    def apply_style(self):
        c = self.colors = dict(DARK if self.theme == "dark" else LIGHT)
        c["text"] = c["ink"]
        self.body_font.configure(size=self.font_size)
        self.title_font.configure(size=self.font_size + 8)
        self.heading_font.configure(size=self.font_size + 1)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont", "TkFixedFont"):
            tkfont.nametofont(name).configure(family="Vazirmatn", size=self.font_size)
        self.root.option_add("*Font", self.body_font)
        self.root.configure(bg=c["bg"])
        s = self.style
        s.configure(".", background=c["bg"], foreground=c["ink"], font=self.body_font, bordercolor=c["line"])
        s.configure("TFrame", background=c["bg"])
        s.configure("TLabel", background=c["bg"], foreground=c["ink"])
        s.configure("Muted.TLabel", foreground=c["muted"])
        s.configure("Title.TLabel", font=self.title_font)
        s.configure("Heading.TLabel", font=self.heading_font)
        s.configure("Card.TFrame", background=c["card"])
        s.configure("Card.TLabel", background=c["card"])
        s.configure("Metric.TLabel", background=c["card"], font=self.heading_font)
        s.configure("TButton", padding=(12, 7), background=c["card"], foreground=c["ink"], borderwidth=1)
        s.map("TButton", background=[("disabled", c["bg"]), ("active", c["active"])], foreground=[("disabled", c["muted"])], bordercolor=[("disabled", c["line"])], relief=[("disabled", "flat")])
        s.configure("Accent.TButton", background=c["blue"], foreground="white", bordercolor=c["blue"])
        s.map("Accent.TButton", background=[("active", "#007338"), ("disabled", c["line"])], foreground=[("disabled", c["muted"]), ("!disabled", "white")])
        for widget in ("TEntry", "TSpinbox", "TCombobox"):
            s.configure(widget, fieldbackground=c["card"], foreground=c["ink"], insertcolor=c["ink"], padding=6, arrowcolor=c["ink"])
        s.map("TCombobox", fieldbackground=[("readonly", c["card"])], foreground=[("readonly", c["ink"])], selectbackground=[("readonly", c["card"])], selectforeground=[("readonly", c["ink"])])
        for widget in ("TCheckbutton", "TRadiobutton"):
            s.map(widget, background=[("active", c["bg"])])
        s.configure("TNotebook", borderwidth=0)
        s.configure("TNotebook.Tab", padding=(20, 8), background=c["bg"])
        s.map("TNotebook.Tab", background=[("selected", c["card"])], foreground=[("selected", c["blue"])])
        s.configure("Treeview", rowheight=self.body_font.metrics("linespace") + 18, fieldbackground=c["card"], background=c["card"], foreground=c["ink"], font=self.body_font, borderwidth=0)
        s.configure("Treeview.Heading", padding=8, font=self.heading_font, background=c["card"], foreground=c["muted"], relief="flat")
        s.map("Treeview", background=[("selected", c["active"])], foreground=[("selected", c["ink"])])
        if hasattr(self, "log"):
            self.log.configure(bg=c["card"], fg=c["ink"])
        if hasattr(self, "settings_canvas"):
            self.settings_canvas.configure(bg=c["bg"])

    def build_ui(self):
        shell = ttk.Frame(self.root, padding=22)
        shell.pack(fill="both", expand=True)
        header = Flow(shell, gap=12)
        header.pack(fill="x", pady=(0, 16))
        brand = ttk.Frame(header)
        header.add(brand, "right")
        ttk.Label(brand, text="فایل‌نظم", style="Title.TLabel", anchor="e").pack(fill="x")
        ttk.Label(brand, text="هر فایل، در جای درست  •  " + VERSION, style="Muted.TLabel", anchor="e").pack(fill="x")
        self.start_button = ttk.Button(header, text="شروع نظارت", command=self.toggle_monitoring, style="Accent.TButton")
        header.add(self.start_button, "left")
        self.status = tk.StringVar(value="آزمایشی • بدون عملیات واقعی" if self.demo else "آماده")
        cards = ttk.Frame(shell)
        cards.pack(fill="x", pady=(0, 16))
        self.count, self.active, self.monitor_state = tk.StringVar(), tk.StringVar(), tk.StringVar(value="متوقف")
        for col, (title, var) in enumerate((("وضعیت نظارت", self.monitor_state), ("قوانین فعال", self.active), ("همهٔ قوانین", self.count))):
            cards.columnconfigure(col, weight=1)
            card = ttk.Frame(cards, padding=(16, 10), style="Card.TFrame")
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            ttk.Label(card, text=title, style="Card.TLabel", anchor="e").pack(fill="x")
            ttk.Label(card, textvariable=var, style="Metric.TLabel", anchor="e").pack(fill="x")
        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        rules, logs = [ttk.Frame(self.tabs, padding=12) for _ in range(2)]
        settings_page = ScrollFrame(self.tabs, self.colors["bg"], padding=12)
        self.settings_canvas = settings_page.canvas
        settings = settings_page.inner
        for page, title in ((rules, "قوانین"), (logs, "گزارش عملیات"), (settings_page, "تنظیمات")):
            self.tabs.add(page, text=title)
        toolbar = Flow(rules)
        toolbar.pack(fill="x", pady=(0, 12))
        toolbar.add(ttk.Button(toolbar, text="+ قانون جدید", command=self.add_rule, style="Accent.TButton"), "right")
        for title, callback in (("ویرایش", self.edit_rule), ("حذف", self.delete_rule), ("فعال / غیرفعال", self.toggle_rule)):
            toolbar.add(ttk.Button(toolbar, text=title, command=callback), "right")
        self.undo_button = ttk.Button(toolbar, text="برگرداندن تغییر", command=self.undo_last)
        toolbar.add(self.undo_button, "left")
        self.redo_button = ttk.Button(toolbar, text="لغو برگشت", command=lambda: self.restore(True))
        toolbar.add(self.redo_button, "left")
        table = ttk.Frame(rules)
        table.pack(fill="both", expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(1, weight=1)
        self.tree = ttk.Treeview(table, columns=("action", "condition", "watch", "name", "state"), show="headings", selectmode="browse")
        for key, title, width in (("action", "عملیات", 90), ("condition", "شرایط", 150), ("watch", "پوشهٔ نظارت", 240), ("name", "نام قانون", 200), ("state", "وضعیت", 75)):
            self.tree.heading(key, text=title, anchor="e")
            self.tree.column(key, width=width, minwidth=60, anchor="e")
        vertical, horizontal = ttk.Scrollbar(table, command=self.tree.yview), ttk.Scrollbar(table, command=self.tree.xview, orient="horizontal")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.grid(row=0, column=0, sticky="ns")
        self.tree.grid(row=0, column=1, sticky="nsew")
        horizontal.grid(row=1, column=1, sticky="ew")
        self.tree.bind("<Double-1>", lambda event: self.edit_rule())
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.selection_changed())
        footer = Flow(rules, gap=12)
        footer.pack(fill="x", pady=(12, 0))
        self.preview_button = ttk.Button(footer, text="پیش‌نمایش تصویری قانون", style="Accent.TButton", command=self.preview)
        footer.add(self.preview_button, "right")
        self.hint = tk.StringVar(value="یک قانون انتخاب کنید")
        footer.add(ttk.Label(footer, textvariable=self.hint, style="Muted.TLabel"), "right")
        self.log = tk.Text(logs, wrap="word", state="disabled", font=self.body_font, bg=self.colors["card"], fg=self.colors["ink"], relief="flat", padx=12, pady=12)
        logbar = ttk.Scrollbar(logs, command=self.log.yview)
        self.log.configure(yscrollcommand=logbar.set)
        logbar.pack(side="left", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("rtl", justify="right")
        self.build_settings(settings)
        wrap_to_width(ttk.Label(shell, text="Ctrl+Z برگرداندن تغییر  •  Ctrl+Y لغو برگشت  •  «برگرداندن» آخرین کار را پس می‌گیرد: تغییر قوانین، یا جابه‌جایی، کپی و حذف فایل", style="Muted.TLabel", anchor="e", justify="right")).pack(fill="x", pady=(10, 0))

    def build_settings(self, page):
        ttk.Label(page, text="ظاهر برنامه", style="Heading.TLabel", anchor="e").pack(fill="x", pady=8)
        row = Flow(page, gap=14)
        row.pack(fill="x", pady=8)
        self.theme_var = tk.StringVar(value=self.theme)
        self.size_var = tk.StringVar(value=str(self.font_size))
        row.add(ttk.Label(row, text="تم برنامه"))
        for title, value in (("سفید / روشن", "light"), ("سیاه / تیره", "dark")):
            row.add(ttk.Radiobutton(row, text=title, value=value, variable=self.theme_var))
        row = Flow(page, gap=10)
        row.pack(fill="x", pady=8)
        row.add(ttk.Label(row, text="اندازهٔ وزیرمتن (۹ تا ۱۸)"))
        row.add(ttk.Spinbox(row, from_=9, to=18, textvariable=self.size_var, width=5, justify="center"))
        ttk.Button(page, text="اعمال و ذخیرهٔ ظاهر", command=self.save_appearance, style="Accent.TButton").pack(anchor="e", pady=10)
        self.startup = tk.BooleanVar(value=self.startup_enabled() if not self.demo else False)
        ttk.Checkbutton(page, text="اجرای خودکار با ویندوز", variable=self.startup, command=self.change_startup).pack(anchor="e", pady=10)
        wrap_to_width(ttk.Label(page, text="وزیرمتن همراه برنامه است؛ نصب فونت در ویندوز لازم نیست.", anchor="e", justify="right", style="Muted.TLabel")).pack(fill="x", pady=4)
        ttk.Label(page, text="پشتیبان قوانین", style="Heading.TLabel", anchor="e").pack(fill="x", pady=(14, 4))
        row = Flow(page, gap=8)
        row.pack(fill="x", pady=4)
        row.add(ttk.Button(row, text="ذخیرهٔ قوانین در فایل", command=self.export_rules))
        row.add(ttk.Button(row, text="افزودن قوانین از فایل", command=self.import_rules))
        ttk.Button(page, text="خروج کامل", command=self.quit).pack(anchor="e", pady=8)

    def selected(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def refresh(self, index=None):
        index = self.selected() if index is None else index
        self.tree.delete(*self.tree.get_children())
        for i, rule in enumerate(self.data["rules"]):
            conditions = rule.get("conditions") or {}
            description = ", ".join(conditions.get("extensions") or []) or "همهٔ پسوندها"
            if set(conditions) - {"extensions"}:
                description += " + شروط بیشتر"
            self.tree.insert("", "end", iid=str(i), values=("، ".join(ACTIONS[a["type"]] for a in rule["actions"]), description, rule["watch"], rule["name"], "فعال" if rule.get("enabled", True) else "غیرفعال"))
        if self.data["rules"] and index is not None:
            self.tree.selection_set(str(min(index, len(self.data["rules"]) - 1)))
        self.count.set(str(len(self.data["rules"])))
        self.active.set(str(sum(bool(r.get("enabled", True)) for r in self.data["rules"])))
        self.update_undo()
        self.selection_changed()

    def selection_changed(self):
        index = self.selected()
        self.preview_button.configure(state="normal" if index is not None else "disabled")
        self.hint.set(self.data["rules"][index]["name"] if index is not None else "یک قانون انتخاب کنید")

    def persist(self, data):
        if self.config_error:
            messagebox.showerror("ذخیره غیرفعال است", "فایل تنظیمات را اصلاح و برنامه را دوباره اجرا کنید.", parent=self.root)
            return False
        try:
            if not self.demo:
                save_config(self.path, data)
            return True
        except Exception as exc:
            messagebox.showerror("ذخیره انجام نشد", str(exc), parent=self.root)
            return False

    def commit(self, rules, record=True, selected=None, label="تغییر قانون‌ها", message="قوانین به‌روزرسانی شدند"):
        data = deepcopy(self.data)
        data["rules"] = deepcopy(rules)
        if not self.persist(data):
            return False
        if record:
            self.history.record(self.data["rules"], label, selected)
            self.order.append("rules")
            del self.order[:-300]
        running = self.engine is not None
        if running:
            self.stop()
        self.data = data
        self.refresh(selected)
        self.write_log(message)
        if running and any(r.get("enabled", True) for r in rules):
            self.start()
        return True

    def add_rule(self):
        editor = RuleEditor(self)
        self.root.wait_window(editor)
        if editor.result:
            self.commit(self.data["rules"] + [editor.result], selected=len(self.data["rules"]), label="افزودن قانون")

    def edit_rule(self):
        index = self.selected()
        if index is None:
            return
        editor = RuleEditor(self, self.data["rules"][index])
        self.root.wait_window(editor)
        if editor.result and editor.result != self.data["rules"][index]:
            rules = deepcopy(self.data["rules"])
            rules[index] = editor.result
            self.commit(rules, selected=index, label="ویرایش قانون")

    def delete_rule(self):
        index = self.selected()
        if index is not None and messagebox.askyesno("حذف قانون", "قانون حذف شود؟ این تغییر قابل آندو است.", parent=self.root):
            rules = deepcopy(self.data["rules"])
            rules.pop(index)
            self.commit(rules, selected=index, label="حذف قانون")

    def toggle_rule(self):
        index = self.selected()
        if index is not None:
            rules = deepcopy(self.data["rules"])
            rules[index]["enabled"] = not rules[index].get("enabled", True)
            self.commit(rules, selected=index, label="فعال/غیرفعال کردن قانون")

    def restore(self, redo=False):
        label = self.history.next_label(redo)
        if not label:
            return
        before = deepcopy(self.history)
        entry = self.history.restore(self.data["rules"], redo)
        message = ("لغو برگشت: " if redo else "برگردانده شد: ") + entry["label"]
        if not self.commit(entry["rules"], record=False, selected=entry["index"], message=message):
            self.history = before
            self.refresh()

    def next_undo(self):
        """Kind of the most recent undoable action: 'files' (real file operation), 'rules' (rule list) or None."""
        while self.order and (self.order[-1] == "files" and not self.file_ops or self.order[-1] == "rules" and not self.history.next_label(False)):
            self.order.pop()
        if self.order:
            return self.order[-1]
        return "rules" if self.history.next_label(False) else None

    def update_undo(self):
        kind = self.next_undo()
        label = None if kind is None else ("جابه‌جایی فایل" if kind == "files" else self.history.next_label(False))
        self.undo_button.configure(state="normal" if kind else "disabled", text="برگرداندن: " + label if label else "برگرداندن تغییر")
        label = self.history.next_label(True)
        self.redo_button.configure(state="normal" if label else "disabled", text="لغو برگشت: " + label if label else "لغو برگشت")

    def undo_last(self):
        kind = self.next_undo()
        if kind == "files":
            self.undo_files()
        elif kind == "rules":
            if self.order and self.order[-1] == "rules":
                self.order.pop()
            self.restore(False)

    def undo_files(self):
        op = self.file_ops[-1]
        action = {"move": "جابه‌جایی", "delete": "حذف"}.get(op["kind"], "کپی")
        if not messagebox.askyesno("برگرداندن", "آخرین {} فایل برگردانده شود؟\n{}".format(action, op["target"]), parent=self.root):
            return
        try:
            message = undo_operation(op, self.engine)
        except Exception as exc:
            # Keep the operation in history when undo fails, so the user can retry.
            self.write_log("برگرداندن انجام نشد: " + str(exc))
            messagebox.showwarning("برگرداندن", str(exc), parent=self.root)
            self.update_undo()
            return
        self.write_log(message)
        self.file_ops.pop()
        if self.order and self.order[-1] == "files":
            self.order.pop()
        self.update_undo()

    def history_shortcut(self, event):
        # Works with English and Persian layouts: match the key code (Z=90, Y=89) or the typed letter.
        keysym = str(getattr(event, "keysym", "")).lower()
        undo = event.keycode == 90 or keysym in ("z", "arabic_zah", "ظ")
        redo = event.keycode == 89 or keysym in ("y", "arabic_ghain", "غ")
        if not (undo or redo) or str(event.widget.winfo_toplevel()) != str(self.root):
            return None
        if event.widget.winfo_class() in ("Text", "Entry", "TEntry", "TCombobox", "TSpinbox", "Spinbox"):
            return None
        self.restore(True) if redo or event.state & 1 else self.undo_last()
        return "break"

    def export_rules(self):
        if not self.data["rules"]:
            messagebox.showinfo("ذخیرهٔ قوانین", "قانونی برای ذخیره وجود ندارد.", parent=self.root)
            return
        name = filedialog.asksaveasfilename(parent=self.root, title="ذخیرهٔ قوانین", defaultextension=".yaml", initialfile="FileNazm-rules.yaml", filetypes=[("فایل قوانین", "*.yaml"), ("همهٔ فایل‌ها", "*.*")])
        if not name:
            return
        try:
            with open(name, "w", encoding="utf-8") as stream:
                yaml.safe_dump({"rules": self.data["rules"]}, stream, allow_unicode=True, sort_keys=False)
            self.write_log("{} قانون در فایل ذخیره شد".format(len(self.data["rules"])))
        except Exception as exc:
            messagebox.showerror("ذخیرهٔ قوانین", str(exc), parent=self.root)

    def import_rules(self):
        name = filedialog.askopenfilename(parent=self.root, title="افزودن قوانین از فایل", filetypes=[("فایل قوانین", "*.yaml *.yml"), ("همهٔ فایل‌ها", "*.*")])
        if not name:
            return
        try:
            with open(name, "r", encoding="utf-8-sig") as stream:
                loaded = yaml.safe_load(stream)
            rules = loaded.get("rules") if isinstance(loaded, dict) else loaded
            if not isinstance(rules, list) or not rules:
                raise ValueError("در این فایل قانونی پیدا نشد")
            for rule in rules:
                validate(rule)
        except Exception as exc:
            messagebox.showerror("افزودن قوانین", "فایل معتبر نیست:\n" + str(exc), parent=self.root)
            return
        if messagebox.askyesno("افزودن قوانین", "{} قانون به قوانین فعلی اضافه شود؟ این تغییر قابل برگرداندن است.".format(len(rules)), parent=self.root):
            first = len(self.data["rules"])
            self.commit(self.data["rules"] + deepcopy(rules), selected=first, label="افزودن قوانین از فایل", message="{} قانون از فایل اضافه شد".format(len(rules)))

    def preview(self):
        index = self.selected()
        if index is not None:
            PreviewDialog(self, self.data["rules"][index])

    def save_appearance(self):
        try:
            size = int(self.size_var.get())
            if not 9 <= size <= 18:
                raise ValueError
        except ValueError:
            messagebox.showerror("اندازهٔ فونت", "عدد صحیح از ۹ تا ۱۸ وارد کنید.", parent=self.root)
            return
        data = deepcopy(self.data)
        data["settings"].update(theme=self.theme_var.get(), font_size=size)
        if self.persist(data):
            self.data, self.theme, self.font_size = data, self.theme_var.get(), size
            self.apply_style()

    def toggle_monitoring(self):
        self.stop() if self.engine else self.start()

    def start(self):
        if self.demo:
            self.write_log("نظارت واقعی در حالت آزمایشی غیرفعال است")
            return
        if self.engine or self.config_error:
            return
        engine = Organizer(self.data["rules"], self.data["settings"], lambda msg: self.events.put(("log", msg)), lambda op: self.events.put(("op", op)))
        try:
            engine.start()
        except Exception as exc:
            engine.stop()
            messagebox.showwarning("نظارت شروع نشد", str(exc), parent=self.root)
            return
        self.engine = engine
        self.start_button.configure(text="توقف نظارت")
        self.monitor_state.set("فعال")
        self.status.set("نظارت فعال است")

    def stop(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.start_button.configure(text="شروع نظارت")
        self.monitor_state.set("متوقف")
        self.status.set("نظارت متوقف است")

    def write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", datetime.now().strftime("%H:%M:%S") + "  |  " + text + "\n", "rtl")
        if int(self.log.index("end-1c").split(".")[0]) > 1000:
            self.log.delete("1.0", "101.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.write_log(value)
            elif kind == "op":
                self.file_ops.append(value)
                for old in self.file_ops[:-200]:
                    discard_backup(old)
                del self.file_ops[:-200]
                self.order.append("files")
                del self.order[:-300]
                self.update_undo()
            elif kind == "show":
                self.root.deiconify()
                self.root.lift()
            elif kind == "start":
                self.start()
            elif kind == "stop":
                self.stop()
            elif kind == "quit":
                self.quit()
                return
        if not self.closing:
            self.poll_job = self.root.after(100, self.poll)

    def setup_tray(self):
        try:
            from native_tray import TrayIcon
            self.tray = TrayIcon(resource("FileNazm.ico"), "فایل‌نظم " + VERSION,
                                 lambda kind: self.events.put((kind, None)))
            self.tray.run_detached()
        except Exception as exc:
            self.tray = None
            self.write_log("آیکون کنار ساعت فعال نشد: " + str(exc))

    def startup_enabled(self):
        if os.name != "nt":
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "FileNazm")
            return True
        except OSError:
            return False

    def change_startup(self):
        if self.demo or os.name != "nt":
            self.startup.set(False)
            return
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                if self.startup.get():
                    args = [sys.executable] + ([] if getattr(sys, "frozen", False) else [str(resource("FileNazm.py"))]) + ["--autostart"]
                    winreg.SetValueEx(key, "FileNazm", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
                else:
                    try:
                        winreg.DeleteValue(key, "FileNazm")
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self.startup.set(self.startup_enabled())
            messagebox.showerror("شروع خودکار", str(exc), parent=self.root)

    def on_close(self):
        self.root.withdraw() if self.tray else self.quit()

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.stop()
        if not self.demo:
            clear_undo_store()
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
        for child in list(self.root.winfo_children()):
            if isinstance(child, PreviewDialog):
                child.close()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
