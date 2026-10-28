"""Windows folder notifications and recycle operations, using only ctypes."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import struct
import threading
from types import SimpleNamespace


def _dll(name):
    if os.name != 'nt': raise OSError('This feature requires Windows')
    return C.WinDLL(name, use_last_error=True)


def _fn(dll, name, args, result):
    fn = getattr(dll, name); fn.argtypes = args; fn.restype = result
    return fn


class SHFILEOPSTRUCTW(C.Structure):
    _fields_ = [('hwnd', W.HWND), ('wFunc', W.UINT), ('pFrom', W.LPCWSTR),
                ('pTo', W.LPCWSTR), ('fFlags', W.WORD), ('fAnyOperationsAborted', W.BOOL),
                ('hNameMappings', W.LPVOID), ('lpszProgressTitle', W.LPCWSTR)]


def send2trash(path):
    path = os.path.abspath(os.fspath(path))
    if '\0' in path or not os.path.isfile(path): raise ValueError('Invalid recycle source')
    # SHFileOperation may permanently delete on non-recyclable volumes. Reject
    # network/removable/long paths rather than silently degrading to unlink.
    kernel = _dll('kernel32')
    drive_type = _fn(kernel, 'GetDriveTypeW', [W.LPCWSTR], W.UINT)
    if drive_type(os.path.splitdrive(path)[0] + '\\') != 3 or len(path) >= 260:
        raise OSError('انتقال امن به سطل بازیافت برای این مسیر پشتیبانی نمی‌شود')
    source = C.create_unicode_buffer(path + '\0\0')
    op = SHFILEOPSTRUCTW()
    op.wFunc, op.pFrom = 3, C.cast(source, W.LPCWSTR)
    # ALLOWUNDO + NOERRORUI. Keep shell confirmation: it can warn when the
    # user disabled the bin or a file exceeds its capacity. Never auto-confirm.
    op.fFlags = 0x0040 | 0x0400 | 0x4000  # WANTNUKEWARNING: warn before permanent deletion
    result = _fn(_dll('shell32'), 'SHFileOperationW', [C.POINTER(SHFILEOPSTRUCTW)], C.c_int)(C.byref(op))
    if result or op.fAnyOperationsAborted:
        raise OSError(result, 'انتقال به سطل بازیافت انجام نشد یا لغو شد')


class FileSystemEventHandler:
    pass


class OVERLAPPED(C.Structure):
    _fields_ = [('Internal', C.c_size_t), ('InternalHigh', C.c_size_t),
                ('Offset', W.DWORD), ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]


def notifications(raw):
    """Validate FILE_NOTIFY_INFORMATION offsets before yielding action/name."""
    offset = 0
    while offset < len(raw):
        if offset + 12 > len(raw): raise ValueError('Truncated directory notification')
        next_offset, action, length = struct.unpack_from('<III', raw, offset)
        if length % 2 or offset + 12 + length > len(raw): raise ValueError('Invalid notification name')
        yield action, raw[offset + 12:offset + 12 + length].decode('utf-16-le')
        if not next_offset: break
        if next_offset < 12 + length or next_offset % 4: raise ValueError('Invalid notification offset')
        offset += next_offset


class Observer:
    def __init__(self):
        self.watches, self.threads, self.handles = [], [], []
        self.stopping = threading.Event()
        self.stop_handle = None
        self.started = []
        self.errors = []

    def schedule(self, handler, path, recursive=False):
        self.watches.append((handler, os.path.abspath(path), recursive))

    def start(self):
        k = self.k = _dll('kernel32')
        self.create = _fn(k, 'CreateFileW', [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE)
        self.event = _fn(k, 'CreateEventW', [W.LPVOID, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE)
        self.close = _fn(k, 'CloseHandle', [W.HANDLE], W.BOOL)
        self.set_event = _fn(k, 'SetEvent', [W.HANDLE], W.BOOL)
        self.reset = _fn(k, 'ResetEvent', [W.HANDLE], W.BOOL)
        self.read = _fn(k, 'ReadDirectoryChangesW', [W.HANDLE, W.LPVOID, W.DWORD, W.BOOL, W.DWORD, C.POINTER(W.DWORD), C.POINTER(OVERLAPPED), W.LPVOID], W.BOOL)
        self.wait = _fn(k, 'WaitForMultipleObjects', [W.DWORD, C.POINTER(W.HANDLE), W.BOOL, W.DWORD], W.DWORD)
        self.cancel = _fn(k, 'CancelIoEx', [W.HANDLE, C.POINTER(OVERLAPPED)], W.BOOL)
        self.result = _fn(k, 'GetOverlappedResult', [W.HANDLE, C.POINTER(OVERLAPPED), C.POINTER(W.DWORD), W.BOOL], W.BOOL)
        self.stop_handle = self.event(None, True, False, None)
        if not self.stop_handle: raise C.WinError(C.get_last_error())
        try:
            for handler, path, recursive in self.watches:
                h = self.create(path, 1, 7, None, 3, 0x02000000 | 0x40000000, None)
                if h == C.c_void_p(-1).value: raise C.WinError(C.get_last_error())
                self.handles.append(h)
            for watch, handle in zip(self.watches, self.handles):
                ready = threading.Event(); self.started.append(ready)
                thread = threading.Thread(target=self._run, args=(*watch, handle, ready), daemon=True, name='ReadDirectoryChangesW')
                self.threads.append(thread); thread.start()
            for ready in self.started:
                if not ready.wait(10): raise OSError('Directory watch startup timeout')
            if self.errors: raise self.errors[0]
        except Exception:
            self.stop(); self.join(); raise

    def _run(self, handler, path, recursive, handle, ready):
        event = self.event(None, True, False, None)
        pending = False
        ov = OVERLAPPED(); ov.hEvent = event
        count = W.DWORD(); buffer = C.create_string_buffer(65536)
        try:
            if not event: raise C.WinError(C.get_last_error())
            while not self.stopping.is_set():
                self.reset(event)
                ok = self.read(handle, buffer, len(buffer), recursive, 0x1 | 0x2 | 0x8 | 0x10, None, C.byref(ov), None)
                if not ok and C.get_last_error() != 997: raise C.WinError(C.get_last_error())
                pending = True
                ready.set()
                index = self.wait(2, (W.HANDLE * 2)(self.stop_handle, event), False, 0xffffffff)
                if index == 0: break
                if index != 1: raise C.WinError(C.get_last_error())
                ok = self.result(handle, C.byref(ov), C.byref(count), False); pending = False
                if not ok: raise C.WinError(C.get_last_error())
                if not count.value:
                    # Buffer overflow: reconcile files instead of losing events.
                    handler.owner.emit('سرریز رویدادهای پوشه؛ بازبینی فایل‌ها: ' + path)
                    for root, dirs, files in os.walk(path):
                        for name in files: handler.owner.enqueue(os.path.join(root, name), handler.folder)
                        if not recursive: break
                    continue
                for action, name in notifications(buffer.raw[:count.value]):
                    full = os.path.join(path, name)
                    event_data = SimpleNamespace(src_path=full, dest_path=full, is_directory=os.path.isdir(full))
                    method = {1: 'on_created', 3: 'on_modified', 5: 'on_moved'}.get(action)
                    if method: getattr(handler, method)(event_data)
        except Exception as exc:
            if not ready.is_set(): self.errors.append(exc)
            if not self.stopping.is_set(): handler.owner.emit('خطای نظارت پوشه: ' + str(exc))
        finally:
            ready.set()
            if pending:
                self.cancel(handle, C.byref(ov))
                self.result(handle, C.byref(ov), C.byref(count), True)
            if event: self.close(event)

    def stop(self):
        self.stopping.set()
        if self.stop_handle: self.set_event(self.stop_handle)

    def is_alive(self):
        return any(t.is_alive() for t in self.threads)

    def join(self):
        for thread in self.threads: thread.join()
        for handle in self.handles: self.close(handle)
        self.handles.clear()
        if self.stop_handle:
            self.close(self.stop_handle); self.stop_handle = None
