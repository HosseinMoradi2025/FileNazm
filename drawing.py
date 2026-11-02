"""Windows GDI text masks, including Persian shaping on Windows 7."""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from resources import resource


@lru_cache(maxsize=384)
def text_mask(text, width, height, pixels, bold=False, align="left"):
    if os.name != "nt":
        raise OSError("Persian preview text rendering requires Windows GDI")
    gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
    class BitmapHeader(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                    ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                    ("image_size", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                    ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
    class BitmapInfo(ctypes.Structure):
        _fields_ = [("header", BitmapHeader), ("colors", wintypes.DWORD * 3)]
    handle = wintypes.HANDLE
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [handle], handle
    gdi.CreateDIBSection.argtypes = [handle, ctypes.POINTER(BitmapInfo), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), handle, wintypes.DWORD]
    gdi.CreateDIBSection.restype = handle
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [handle, handle], handle
    gdi.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi.CreateFontW.restype = handle
    gdi.DeleteObject.argtypes = [handle]
    gdi.DeleteDC.argtypes = [handle]
    gdi.SetTextColor.argtypes = [handle, wintypes.DWORD]
    gdi.SetBkColor.argtypes = [handle, wintypes.DWORD]
    user.DrawTextW.argtypes = [handle, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    dc, bitmap, font = gdi.CreateCompatibleDC(None), None, None
    old_bitmap, old_font = None, None
    try:
        info = BitmapInfo()
        info.header = BitmapHeader(ctypes.sizeof(BitmapHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise RuntimeError("ساخت تصویر متن ناموفق بود")
        ctypes.memset(bits, 0, width * height * 4)
        old_bitmap = gdi.SelectObject(dc, bitmap)
        font = gdi.CreateFontW(-pixels, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 4, 0, "Vazirmatn")
        old_font = gdi.SelectObject(dc, font)
        gdi.SetTextColor(dc, 0xFFFFFF)
        gdi.SetBkColor(dc, 0)
        rect = wintypes.RECT(0, 0, width, height)
        flags = 0x20 | 0x4 | 0x8000  # single line, vertically centered, ellipsis
        flags |= 2 if align == "right" else 1 if align == "center" else 0
        if any("\u0600" <= c <= "\u06ff" for c in text):
            flags |= 0x20000  # RTLREADING; GDI performs Arabic/Persian shaping
        user.DrawTextW(dc, text, len(text.encode("utf-16-le")) // 2, ctypes.byref(rect), flags)
        gdi.GdiFlush()
        raw = ctypes.string_at(bits, width * height * 4)
        return raw[0::4]  # grayscale GDI antialiasing: B == G == R
    finally:
        if old_font:
            gdi.SelectObject(dc, old_font)
        if old_bitmap:
            gdi.SelectObject(dc, old_bitmap)
        if font:
            gdi.DeleteObject(font)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)


def label(image, text, box, color, pixels=18, bold=False, align="left"):
    x, y, width, height = box
    image.paste(color, (x, y, x + width, y + height), text_mask(str(text), width, height, pixels, bold, align))


def file_icon(draw, x, y, color, small=False):
    w, h = (22, 28) if small else (32, 40)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=4, fill=color)
    draw.polygon([(x + w - 9, y), (x + w - 9, y + 9), (x + w, y + 9)], fill="#d8f0e1")
    for offset in (15, 20):
        draw.line((x + 5, y + offset, x + w - 5, y + offset), fill="white", width=1)
