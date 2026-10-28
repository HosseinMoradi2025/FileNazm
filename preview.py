"""Responsive batch GIF player. No settings or test files are written."""
from copy import deepcopy
from io import BytesIO
from pathlib import PureWindowsPath
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from raster import Image, ImageTk
from animation import folder_of, make_gif, render_frame, summary
from flow import Flow, wrap_to_width
from model import Sample, make_samples, simulate


class PreviewDialog(tk.Toplevel):
    def __init__(self, app, rule):
        super().__init__(app.root)
        self.app, self.rule = app, deepcopy(rule)
        self.samples = make_samples(rule)
        self.steps = simulate(rule, self.samples, app.data["settings"])
        self.title("پیش‌نمایش تصویری قانون — " + rule["name"])
        self.geometry("{}x{}".format(min(1080, self.winfo_screenwidth() - 80), min(850, self.winfo_screenheight() - 100)))
        self.minsize(440, 420)
        self.configure(bg=app.colors["bg"])
        self.transient(app.root)
        self.job, self.poll_job, self.gif, self.photo, self.current_image = None, None, None, None, None
        self.index, self.playing, self.generation, self.closed = 0, False, 0, False
        self.cancel, self.results = threading.Event(), queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self.close)
        shell = ttk.Frame(self, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text=rule["name"], style="Title.TLabel", anchor="e").pack(fill="x")
        wrap_to_width(ttk.Label(shell, text="پیش‌نمایش خودکار با ۶ فایل خیالی • هیچ فایلی روی دیسک ساخته یا تغییر داده نمی‌شود", anchor="e", justify="right", style="Muted.TLabel")).pack(fill="x", pady=(2, 12))
        self.canvas = tk.Canvas(shell, background=app.colors["bg"], highlightthickness=0, width=300, height=200)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw_current())
        self.canvas_image = self.canvas.create_image(0, 0, anchor="center")
        self.status = tk.StringVar(value="در حال آماده‌سازی انیمیشن…")
        wrap_to_width(ttk.Label(shell, textvariable=self.status, anchor="e", justify="right", style="Heading.TLabel")).pack(fill="x", pady=(8, 6))
        controls = Flow(shell, gap=6)
        controls.pack(fill="x", pady=6)
        self.play_button = ttk.Button(controls, text="مکث", command=self.toggle, state="disabled", style="Accent.TButton")
        controls.add(self.play_button, "right")
        controls.add(ttk.Button(controls, text="پخش دوباره", command=self.restart), "right")
        controls.add(ttk.Button(controls, text="فایل‌های نمونه", command=self.edit_samples), "right")
        controls.add(ttk.Button(controls, text="جزئیات نتیجه", command=self.details), "left")
        self.speed = tk.StringVar(value="1×")
        controls.add(ttk.Combobox(controls, textvariable=self.speed, values=["0.5×", "1×", "1.5×", "2×"], state="readonly", width=5), "left")
        self.loop = tk.BooleanVar(value=False)
        controls.add(ttk.Checkbutton(controls, text="تکرار", variable=self.loop), "left")
        wrap_to_width(ttk.Label(shell, text="آبی: در حال بررسی  •  سبز: انجام‌شده  •  خاکستری: بدون تغییر", style="Muted.TLabel", anchor="e", justify="right")).pack(fill="x", pady=(6, 0))
        self.regenerate()

    def regenerate(self):
        self.cancel.set()
        self.cancel = threading.Event()
        self.generation += 1
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.playing = False
        if self.gif:
            self.gif.close()
            self.gif = None
        self.steps = simulate(self.rule, self.samples, self.app.data["settings"])
        self.current_image = render_frame(self.rule, self.samples, self.steps, 0, 0, self.app.theme == "dark", self.app.font_size)
        self.draw_current()
        self.status.set("در حال آماده‌سازی انیمیشن…")
        self.play_button.configure(state="disabled")
        generation, cancel = self.generation, self.cancel
        args = (deepcopy(self.rule), list(self.samples), list(self.steps), self.app.theme == "dark", self.app.font_size)
        def build():
            try:
                data = make_gif(*args, cancel=cancel)
                self.results.put((generation, data, None))
            except Exception as exc:
                self.results.put((generation, None, str(exc)))
        threading.Thread(target=build, daemon=True, name="VirtualGifBuilder").start()
        if self.poll_job is None:
            self.poll_job = self.after(80, self.collect)

    def collect(self):
        self.poll_job = None
        if self.closed:
            return
        try:
            while True:
                generation, data, error = self.results.get_nowait()
                if generation != self.generation:
                    continue
                if error:
                    self.status.set("ساخت پیش‌نمایش انجام نشد")
                    messagebox.showerror("پیش‌نمایش", error, parent=self)
                    return
                if data:
                    self.gif_bytes = data
                    self.gif = Image.open(BytesIO(data))
                    self.play_button.configure(state="normal")
                    self.restart()
                    return
        except queue.Empty:
            self.poll_job = self.after(80, self.collect)

    def draw_current(self):
        if self.current_image is None or self.closed:
            return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = min(width / 1000, height / 550)
        if scale <= .05:
            return
        resized = self.current_image.resize((max(1, int(1000 * scale)), max(1, int(550 * scale))), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized, master=self)
        self.canvas.coords(self.canvas_image, width / 2, height / 2)
        self.canvas.itemconfigure(self.canvas_image, image=self.photo)

    def tick(self):
        self.job = None
        if not self.playing or self.gif is None or self.closed:
            return
        if self.index >= self.gif.n_frames:
            if self.loop.get():
                self.index = 0
            else:
                self.playing = False
                self.play_button.configure(text="پخش")
                changed, unchanged = summary(self.steps)
                self.status.set("پایان پیش‌نمایش: {} فایل تغییر کرد و {} فایل بدون تغییر ماند".format(changed, unchanged))
                return
        self.gif.seek(self.index)
        self.current_image = self.gif.convert("RGB")
        self.draw_current()
        delay = max(20, int(self.gif.info.get("duration", 90) / float(self.speed.get().rstrip("×"))))
        self.index += 1
        self.job = self.after(delay, self.tick)

    def restart(self):
        if self.gif is None:
            return
        if self.job:
            self.after_cancel(self.job)
        self.index, self.playing = 0, True
        self.play_button.configure(text="مکث")
        self.status.set("در حال نمایش نتیجهٔ قانون روی فایل‌های خیالی")
        self.tick()

    def toggle(self):
        if self.gif is None:
            return
        if self.index >= self.gif.n_frames:
            self.restart()
            return
        self.playing = not self.playing
        self.play_button.configure(text="مکث" if self.playing else "پخش")
        if self.playing:
            self.status.set("در حال نمایش نتیجهٔ قانون روی فایل‌های خیالی")
            if self.job is None:
                self.tick()
        else:
            self.status.set("پخش متوقف است؛ فایل‌های واقعی تغییری نکرده‌اند")
            if self.job:
                self.after_cancel(self.job)
                self.job = None

    def details(self):
        window = tk.Toplevel(self)
        window.title("جزئیات نتیجهٔ فرضی")
        window.geometry("820x500")
        window.minsize(360, 260)
        window.transient(self)
        text = tk.Text(window, font=self.app.body_font, wrap="word", padx=18, pady=18, bg=self.app.colors["card"], fg=self.app.colors["text"])
        bar = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="left", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("rtl", justify="right")
        for step in self.steps:
            folder = "\nپوشهٔ مقصد: " + folder_of(step)[0] if step.action in ("move", "copy") else ""
            text.insert("end", "{} — {}\nمبدأ: {}\nنتیجه: {}{}\n\n".format(self.samples[step.sample_index].name, step.reason, step.source, step.destination, folder), "rtl")
        text.insert("end", "پوشه‌های واقعی، مجوزها و فایل‌های موجود بررسی نمی‌شوند. محتوای نمونه فرضی است؛ استخراج متن PDF/Word واقعی در پیش‌نمایش انجام نمی‌شود.", "rtl")
        text.configure(state="disabled")

    def edit_samples(self):
        window = tk.Toplevel(self)
        window.title("ویرایش فایل‌های خیالی")
        window.geometry("820x490")
        window.minsize(360, 300)
        window.transient(self)
        window.grab_set()
        box = ttk.Frame(window, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="این مقادیر فقط دادهٔ آزمایشی هستند", style="Heading.TLabel", anchor="e").pack(fill="x", pady=(0, 12))
        form = ttk.Frame(box)
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=2)
        form.columnconfigure(2, weight=2)
        for col, title in enumerate(("محتوای فرضی", "حجم MB", "نام فایل")):
            ttk.Label(form, text=title, anchor="e").grid(row=0, column=col, sticky="ew", padx=4)
        variables = []
        for row, sample in enumerate(self.samples, 1):
            content, size, name = tk.StringVar(value=sample.content), tk.StringVar(value=str(sample.size_mb)), tk.StringVar(value=sample.name)
            variables.append((name, size, content))
            for col, variable in enumerate((content, size, name)):
                ttk.Entry(form, textvariable=variable, width=8 if col == 1 else 25, justify="right").grid(row=row, column=col, sticky="ew", pady=6, padx=4)
        def apply():
            try:
                samples, seen = [], set()
                for name, size, content in variables:
                    value, number = name.get().strip(), float(size.get())
                    if not value or PureWindowsPath(value).name != value or any(c in value for c in '<>:"/\\|?*') or not math.isfinite(number) or number < 0:
                        raise ValueError("نام سادهٔ فایل و حجم نامنفی وارد کنید")
                    if value.casefold() in seen:
                        raise ValueError("نام فایل‌های خیالی نباید تکراری باشد")
                    seen.add(value.casefold())
                    samples.append(Sample(value, number, content.get()))
                self.samples = samples
                window.destroy()
                self.regenerate()
            except ValueError as exc:
                messagebox.showerror("دادهٔ نامعتبر", str(exc), parent=window)
        ttk.Button(box, text="اعمال و پخش پیش‌نمایش", style="Accent.TButton", command=apply).pack(anchor="e", pady=(14, 0))

    def close(self):
        self.closed = True
        self.cancel.set()
        for job in (self.job, self.poll_job):
            if job:
                self.after_cancel(job)
        if self.gif:
            self.gif.close()
        self.destroy()
