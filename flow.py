"""Responsive helpers: Flow wraps buttons onto extra rows; ScrollFrame scrolls a page that is too tall."""
import tkinter as tk
from tkinter import ttk


class Flow(ttk.Frame):
    """Right-to-left row of widgets. Wraps onto several rows when the window is narrow, so nothing is cut off."""

    def __init__(self, parent, gap=6, **kw):
        super().__init__(parent, **kw)
        self.gap, self.items, self.size, self.busy = gap, [], None, False
        self.bind("<Configure>", lambda event: self.layout())

    def add(self, widget, side="right"):
        self.items.append((widget, side))
        widget.bind("<Configure>", lambda event: self.layout(), add="+")
        self.layout()
        return widget

    def layout(self):
        width = self.winfo_width()
        if self.busy or width <= 1 or not self.items:
            return
        self.busy = True
        try:
            sizes = []
            for widget, side in self.items:
                if isinstance(widget, ttk.Label) and str(widget.cget("wraplength")) != "0":
                    widget.configure(wraplength=0)
                w, h = widget.winfo_reqwidth(), widget.winfo_reqheight()
                if w > width and isinstance(widget, ttk.Label):
                    widget.configure(wraplength=max(60, width - 8))
                    w, h = widget.winfo_reqwidth(), widget.winfo_reqheight()
                sizes.append((widget, side, min(w, width), h, w > width))
            total = sum(s[2] for s in sizes) + self.gap * (len(sizes) - 1)
            if total <= width:
                right, left, height = width, 0, max(s[3] for s in sizes)
                for widget, side, w, h, clamp in sizes:
                    if side == "right":
                        widget.place(x=right, y=0, anchor="ne", width=w if clamp else "")
                        right -= w + self.gap
                    else:
                        widget.place(x=left, y=0, anchor="nw", width=w if clamp else "")
                        left += w + self.gap
            else:
                ordered = [s for s in sizes if s[1] == "right"] + [s for s in sizes if s[1] != "right"]
                x, y, row_h = width, 0, 0
                for widget, side, w, h, clamp in ordered:
                    if x < width and x - w < 0:
                        y += row_h + self.gap
                        x, row_h = width, 0
                    widget.place(x=x, y=y, anchor="ne", width=w if clamp else "")
                    x -= w + self.gap
                    row_h = max(row_h, h)
                height = y + row_h
            if self.size != height:
                self.size = height
                self.configure(height=height)
        finally:
            self.busy = False


class ScrollFrame(ttk.Frame):
    """Vertical scrolling page; the scrollbar appears only when the content does not fit."""

    def __init__(self, parent, bg, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=bg)
        self.bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda event: self.update_region())
        self.canvas.bind("<Configure>", lambda event: self.update_region())
        self.canvas.bind("<Enter>", lambda event: self.canvas.bind_all("<MouseWheel>", self.wheel))
        self.canvas.bind("<Leave>", lambda event: self.canvas.unbind_all("<MouseWheel>"))

    def update_region(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.itemconfigure(self.window, width=width)
        content = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, width, max(content, height)))
        if content > height:
            self.bar.pack(side="right", fill="y", before=self.canvas)
        else:
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")


def wrap_to_width(label, margin=4):
    """Let a full-width label wrap its text to the available width."""
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(80, event.width - margin)))
    return label
