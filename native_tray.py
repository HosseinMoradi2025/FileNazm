"""Shell_NotifyIconW tray with a dedicated window/message-loop thread."""
import ctypes as C
from ctypes import wintypes as W
import threading
import uuid
from native_files import _dll, _fn

LRESULT = C.c_ssize_t
WNDPROC = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
                ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON),
                ('hCursor', W.HANDLE), ('hbrBackground', W.HANDLE), ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]


class GUID(C.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', C.c_byte * 8)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT),
                ('uCallbackMessage', W.UINT), ('hIcon', W.HICON), ('szTip', W.WCHAR * 128),
                ('dwState', W.DWORD), ('dwStateMask', W.DWORD), ('szInfo', W.WCHAR * 256),
                ('uVersion', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
                ('guidItem', GUID), ('hBalloonIcon', W.HICON)]


class TrayIcon:
    def __init__(self, path, title, callback):
        self.path, self.title, self.callback = str(path), title, callback
        self.ready = threading.Event(); self.error = None; self.hwnd = None

    def run_detached(self):
        self.thread = threading.Thread(target=self._run, name='FileNazmTray', daemon=True)
        self.thread.start()
        if not self.ready.wait(10): raise RuntimeError('Tray startup timeout')
        if self.error: raise self.error

    def stop(self):
        if self.hwnd: self.post(self.hwnd, 0x10, 0, 0)
        if hasattr(self, 'thread'): self.thread.join(timeout=5)

    def _run(self):
        icon = menu = instance = None
        registered = False
        try:
            u, k, s = _dll('user32'), _dll('kernel32'), _dll('shell32')
            default = _fn(u, 'DefWindowProcW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM], LRESULT)
            self.post = _fn(u, 'PostMessageW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL)
            destroy = _fn(u, 'DestroyWindow', [W.HWND], W.BOOL)
            quit_loop = _fn(u, 'PostQuitMessage', [C.c_int], None)
            notify = _fn(s, 'Shell_NotifyIconW', [W.DWORD, C.POINTER(NOTIFYICONDATA)], W.BOOL)
            create_menu = _fn(u, 'CreatePopupMenu', [], W.HMENU)
            append = _fn(u, 'AppendMenuW', [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR], W.BOOL)
            popup = _fn(u, 'TrackPopupMenu', [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, W.LPVOID], W.UINT)
            cursor = _fn(u, 'GetCursorPos', [C.POINTER(W.POINT)], W.BOOL)
            foreground = _fn(u, 'SetForegroundWindow', [W.HWND], W.BOOL)
            restart = _fn(u, 'RegisterWindowMessageW', [W.LPCWSTR], W.UINT)('TaskbarCreated')
            data = NOTIFYICONDATA(); data.cbSize = C.sizeof(data)
            def proc(hwnd, msg, wp, lp):
                try:
                    if msg == restart and restart:
                        notify(0, C.byref(data)); return 0
                    if msg == 0x8001:
                        if lp == 0x203: self.callback('show')
                        elif lp in (0x205, 0x7b):
                            point = W.POINT(); cursor(C.byref(point)); foreground(hwnd)
                            choice = popup(menu, 0x100 | 0x2 | 0x8000, point.x, point.y, 0, hwnd, None)
                            if 1 <= choice <= 4: self.callback(('show', 'start', 'stop', 'quit')[choice - 1])
                            self.post(hwnd, 0, 0, 0)
                        return 0
                    if msg == 0x10: destroy(hwnd); return 0
                    if msg == 2: quit_loop(0); return 0
                except Exception:
                    # Never propagate exceptions across a C callback boundary.
                    self.callback('show')
                return default(hwnd, msg, wp, lp)
            self.proc = WNDPROC(proc)
            instance = _fn(k, 'GetModuleHandleW', [W.LPCWSTR], W.HMODULE)(None)
            classname = 'FileNazmTray_' + uuid.uuid4().hex
            wc = WNDCLASS(); wc.lpfnWndProc = self.proc; wc.hInstance = instance; wc.lpszClassName = classname
            if not _fn(u, 'RegisterClassW', [C.POINTER(WNDCLASS)], W.ATOM)(C.byref(wc)): raise C.WinError(C.get_last_error())
            registered = True
            self.hwnd = _fn(u, 'CreateWindowExW', [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND)(0, classname, self.title, 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd: raise C.WinError(C.get_last_error())
            icon = _fn(u, 'LoadImageW', [W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT], W.HANDLE)(None, self.path, 1, 0, 0, 0x10 | 0x40)
            if not icon: raise C.WinError(C.get_last_error())
            menu = create_menu()
            if not menu: raise C.WinError(C.get_last_error())
            for i, label in enumerate(('نمایش فایل‌نظم', 'شروع نظارت', 'توقف نظارت', 'خروج'), 1): append(menu, 0, i, label)
            _fn(u, 'SetMenuDefaultItem', [W.HMENU, W.UINT, W.UINT], W.BOOL)(menu, 1, 0)
            data.hWnd, data.uID, data.uFlags = self.hwnd, 1, 7
            data.uCallbackMessage, data.hIcon, data.szTip = 0x8001, icon, self.title[:127]
            if not notify(0, C.byref(data)): raise C.WinError(C.get_last_error())
            self.ready.set()
            get = _fn(u, 'GetMessageW', [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], C.c_int)
            translate = _fn(u, 'TranslateMessage', [C.POINTER(W.MSG)], W.BOOL)
            dispatch = _fn(u, 'DispatchMessageW', [C.POINTER(W.MSG)], LRESULT)
            msg = W.MSG()
            while True:
                result = get(C.byref(msg), None, 0, 0)
                if result <= 0: break
                translate(C.byref(msg)); dispatch(C.byref(msg))
            notify(2, C.byref(data))
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
            if self.hwnd:
                destroy(self.hwnd); self.hwnd = None
            if menu: _fn(u, 'DestroyMenu', [W.HMENU], W.BOOL)(menu)
            if icon: _fn(u, 'DestroyIcon', [W.HICON], W.BOOL)(icon)
            if registered: _fn(u, 'UnregisterClassW', [W.LPCWSTR, W.HINSTANCE], W.BOOL)(classname, instance)
