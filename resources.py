"""Bundled resources and process-private font registration (Windows 7 APIs)."""
from pathlib import Path
import ctypes
import os
import sys


def resource(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def register_fonts():
    if os.name == "nt":
        for filename in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"):
            if not ctypes.windll.gdi32.AddFontResourceExW(str(resource("assets/fonts/" + filename)), 0x10, 0):
                raise RuntimeError("بارگذاری فونت وزیرمتن ناموفق بود")
