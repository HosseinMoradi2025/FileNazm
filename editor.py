"""Rule editor: name, folder, extensions, action, destination. Hidden conditions/extra actions of existing rules are preserved."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from model import ACTIONS, extensions, validate
from flow import Flow, wrap_to_width
from preview import PreviewDialog


class RuleEditor(tk.Toplevel):
    def __init__(self, app, rule=None):
        super().__init__(app.root)
        self.app, self.original, self.result = app, deepcopy(rule or {}), None
        self.title("ویرایش قانون" if rule else "افزودن قانون")
        self.transient(app.root)
        self.grab_set()
        self.configure(bg=app.colors["bg"])
        box = ttk.Frame(self, padding=20)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="تعریف قانون", style="Title.TLabel", anchor="e").pack(fill="x", pady=(0, 12))
        page = ttk.Frame(box, padding=(4, 8))
        page.pack(fill="both", expand=True)
        page.columnconfigure(1, weight=1)
        conditions = self.original.get("conditions") or {}
        first = (self.original.get("actions") or [{}])[0]
        self.values = {}
        fields = [("name", "نام قانون", self.original.get("name", ""), False),
                  ("watch", "پوشهٔ نظارت", self.original.get("watch", ""), True),
                  ("extensions", "پسوندها، جدا با کاما", ", ".join(conditions.get("extensions") or []), False)]
        for row, (key, title, value, browse) in enumerate(fields):
            variable = self.values[key] = tk.StringVar(value="" if value is None else str(value))
            ttk.Label(page, text=title, anchor="e").grid(row=row, column=2, sticky="e", padx=(12, 0), pady=7)
            ttk.Entry(page, textvariable=variable, justify="right").grid(row=row, column=1 if browse else 0, columnspan=1 if browse else 2, sticky="ew", pady=7)
            if browse:
                ttk.Button(page, text="انتخاب", command=lambda var=variable: self.browse(var), width=6).grid(row=row, column=0, padx=(0, 6))
        self.action = tk.StringVar(value=ACTIONS.get(first.get("type"), "انتقال"))
        ttk.Label(page, text="عملیات", anchor="e").grid(row=3, column=2, sticky="e", padx=(12, 0), pady=7)
        ttk.Combobox(page, values=list(ACTIONS.values()), textvariable=self.action, state="readonly", justify="right").grid(row=3, column=0, columnspan=2, sticky="ew", pady=7)
        variable = self.values["destination"] = tk.StringVar(value=str(first.get("destination", "")))
        ttk.Label(page, text="پوشهٔ مقصد", anchor="e").grid(row=4, column=2, sticky="e", padx=(12, 0), pady=7)
        ttk.Entry(page, textvariable=variable, justify="right").grid(row=4, column=1, sticky="ew", pady=7)
        ttk.Button(page, text="انتخاب", command=lambda var=variable: self.browse(var), width=6).grid(row=4, column=0, padx=(0, 6))
        wrap_to_width(ttk.Label(page, text="قالب مقصد: {ext}  {name}  {date}  {year}  {month}", anchor="e", justify="right", style="Muted.TLabel")).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        self.enabled = tk.BooleanVar(value=self.original.get("enabled", True))
        self.recycle = tk.BooleanVar(value=first.get("recycle", True))
        self.overwrite = tk.BooleanVar(value=first.get("overwrite", False))
        for row, (title, var) in enumerate((("قانون فعال باشد", self.enabled), ("حذف به سطل بازیافت", self.recycle), ("جایگزینی فایل هم‌نام در مقصد", self.overwrite)), 6):
            ttk.Checkbutton(page, text=title, variable=var).grid(row=row, column=0, columnspan=3, sticky="e", pady=4)
        buttons = Flow(box, gap=8)
        buttons.pack(fill="x", pady=(14, 0))
        buttons.add(ttk.Button(buttons, text="ذخیرهٔ قانون", command=self.save, style="Accent.TButton"), "right")
        buttons.add(ttk.Button(buttons, text="پیش‌نمایش تصویری", command=self.preview), "right")
        buttons.add(ttk.Button(buttons, text="انصراف", command=self.destroy), "left")
        # Fit the window to its content (font size/DPI safe) so no manual resizing is needed.
        self.update_idletasks()
        width, height = max(640, self.winfo_reqwidth() + 20), self.winfo_reqheight() + 20
        width, height = min(width, self.winfo_screenwidth() - 60), min(height, self.winfo_screenheight() - 80)
        self.minsize(380, 300)
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - width) // 2)
        y = app.root.winfo_rooty() + max(0, (app.root.winfo_height() - height) // 3)
        self.geometry("{}x{}+{}+{}".format(width, height, x, y))

    def browse(self, var):
        selected = filedialog.askdirectory(parent=self)
        if selected:
            var.set(selected.rstrip("/\\") + "/")

    def build(self):
        rule = deepcopy(self.original)
        rule.update(name=self.values["name"].get().strip(), watch=self.values["watch"].get().strip(), enabled=self.enabled.get())
        conditions = deepcopy(rule.get("conditions") or {})
        for key in ("extensions",):
            value = self.values[key].get().strip()
            if not value:
                conditions.pop(key, None)
            elif key == "extensions":
                conditions[key] = extensions(value)
            elif key in ("min_size_mb", "max_size_mb"):
                conditions[key] = float(value)
            else:
                conditions[key] = [part.strip() for part in value.replace("،", ",").split("|" if key == "content_contains" else ",") if part.strip()]
        rule["conditions"] = conditions
        actions = deepcopy(rule.get("actions") or [{}])
        action = actions[0]
        action["type"] = next(key for key, title in ACTIONS.items() if title == self.action.get())
        if action["type"] == "delete":
            action["recycle"] = self.recycle.get()
            action.pop("destination", None)
            action.pop("overwrite", None)
        else:
            action.update(destination=self.values["destination"].get().strip(), overwrite=self.overwrite.get())
            action.pop("recycle", None)
        rule["actions"] = actions
        validate(rule)
        return rule

    def save(self):
        try:
            self.result = self.build()
            self.destroy()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("قانون معتبر نیست", str(exc), parent=self)

    def preview(self):
        try:
            rule = self.build()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("قانون معتبر نیست", str(exc), parent=self)
            return
        dialog = PreviewDialog(self.app, rule)
        dialog.transient(self)
        dialog.grab_set()
        self.wait_window(dialog)
        if self.winfo_exists():
            self.grab_set()
