"""Real operations are separate from the in-memory preview. Python 3.8 compatible."""
from copy import deepcopy
import errno
import tempfile
from datetime import datetime
from pathlib import Path, PureWindowsPath
import os
import shutil
import struct
import threading
import time
import uuid
import config_yaml as yaml
from native_files import send2trash, FileSystemEventHandler, Observer
from model import Sample, ignored, matches, render_destination, validate

DEFAULT = {"settings": {"theme": "light", "font_size": 11, "ignore_hidden": True, "debounce_seconds": 2}, "rules": []}


BACKUP_LIMIT = 500 * 1024 ** 2  # bigger files are not backed up, so their deletion/replacement cannot be undone


def undo_dir():
    # Keep each process' undo backups isolated. A second app instance must not
    # delete backups that the first instance still references in memory.
    return config_path().parent / "undo" / str(os.getpid())


def clear_undo_store():
    shutil.rmtree(str(undo_dir()), ignore_errors=True)


def discard_backup(op):
    backup = op.get("backup")
    if backup:
        shutil.rmtree(str(Path(backup).parent), ignore_errors=True)


def make_backup(path):
    """Copy a file into the undo store before it is deleted or replaced. Returns the backup path or None."""
    try:
        if not path.is_file() or path.stat().st_size > BACKUP_LIMIT:
            return None
        folder = undo_dir() / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / path.name
        shutil.copy2(str(path), str(backup))
        return str(backup)
    except OSError:
        return None


def recycle_bin_items(roots):
    """Yield (info_file, data_file, original_path, size, deleted_at) for each item in the given $Recycle.Bin folders.

    Understands both the Windows 7/8 ($I version 1) and Windows 10/11 ($I version 2) record formats."""
    for root in roots:
        try:
            owners = list(Path(root).iterdir())
        except OSError:
            continue
        for owner in owners:
            try:
                infos = [x for x in owner.iterdir() if x.name[:2] == "$I"]
            except OSError:
                continue
            for info in infos:
                try:
                    raw = info.read_bytes()
                    version, size, stamp = struct.unpack_from("<qqq", raw, 0)
                    if version == 2:
                        count = struct.unpack_from("<i", raw, 24)[0]
                        text = raw[28:28 + 2 * max(count, 0)]
                    elif version == 1:
                        text = raw[24:24 + 520]
                    else:
                        continue
                    original = text.decode("utf-16-le").split("\0", 1)[0]
                    data = info.with_name("$R" + info.name[2:])
                    if original and data.is_file():
                        yield info, data, original, size, stamp
                except (OSError, struct.error, UnicodeDecodeError):
                    continue


def find_in_recycle_bin(source, size=None, roots=None):
    """The newest Recycle Bin item that was deleted from `source` (and has the recorded size), or None."""
    if roots is None:
        drive = PureWindowsPath(str(source)).drive
        if os.name != "nt" or len(drive) != 2 or drive[1] != ":":
            return None
        roots = [Path(drive + "\\") / "$Recycle.Bin"]
    wanted = PureWindowsPath(str(source))
    found = [x for x in recycle_bin_items(roots) if PureWindowsPath(x[2]) == wanted and (size is None or x[3] == size)]
    return max(found, key=lambda x: x[4]) if found else None


def restore_from_recycle_bin(source, size=None, roots=None):
    """Take a file back out of the Windows Recycle Bin into its original place. Returns True on success."""
    item = find_in_recycle_bin(source, size, roots)
    if not item:
        return False
    info, data, _, _, _ = item
    destination = Path(source)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data), str(destination))
    except OSError:
        return False
    try:
        info.unlink()  # drop the now-orphaned record so the bin stays tidy
    except OSError:
        pass
    return True


def config_path():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) if os.name == "nt" else Path.home() / ".config"
    return base / "FileNazm" / "rules.yaml"


def load_config(path):
    if not path.exists():
        legacy = path.parent.parent / "Saman" / "rules.yaml"
        if not legacy.exists():
            return deepcopy(DEFAULT)
        path = legacy
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list) or not isinstance(data.get("settings", {}), dict):
        raise ValueError("ساختار تنظیمات معتبر نیست")
    data.setdefault("rules", [])
    data.setdefault("settings", {})
    for rule in data["rules"]:
        validate(rule)
    return data


def save_config(path, data):
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".yaml.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


def extract_content(path, settings):
    if path.stat().st_size > float(settings.get("max_content_scan_mb", 20)) * 1024 ** 2:
        return None
    ext = path.suffix.lower()
    if ext in {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm", ".ini", ".yaml", ".yml"}:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    if ext == ".pdf":
        from document_text import pdf_text
        return pdf_text(path, int(settings.get("max_pdf_pages", 200)))
    if ext == ".docx":
        from document_text import docx_text
        return docx_text(path)
    return None


class Events(FileSystemEventHandler):
    def __init__(self, owner, folder):
        self.owner, self.folder = owner, folder

    def on_created(self, event):
        if not event.is_directory:
            self.owner.enqueue(event.src_path, self.folder)

    def on_moved(self, event):
        if not event.is_directory:
            self.owner.enqueue(event.dest_path, self.folder)

    def on_modified(self, event):
        if self.owner.settings.get("react_to_modified", True) and not event.is_directory:
            self.owner.enqueue(event.src_path, self.folder)


class Organizer:
    def __init__(self, rules, settings, emit, record=None):
        self.rules, self.settings, self.emit, self.record = deepcopy(rules), deepcopy(settings), emit, record
        self.observer, self.stop_event = Observer(), threading.Event()
        self.pending, self.suppressed, self.processed, self.watch_map = {}, {}, {}, {}
        self.lock, self.worker = threading.Lock(), None

    def start(self):
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            validate(rule)
            folder = Path(rule["watch"]).expanduser().resolve()
            if folder.is_dir():
                self.watch_map.setdefault(folder, []).append(rule)
            else:
                self.emit("پوشه یافت نشد: " + str(folder))
        if not self.watch_map:
            raise ValueError("قانون فعال با پوشهٔ معتبر وجود ندارد")
        for folder in self.watch_map:
            self.observer.schedule(Events(self, folder), str(folder), recursive=bool(self.settings.get("recursive", False)))
        self.observer.start()
        self.worker = threading.Thread(target=self._work, daemon=True, name="FileNazmWorker")
        self.worker.start()
        self.emit("نظارت شروع شد")

    def stop(self):
        self.stop_event.set()
        self.observer.stop()
        self.observer.join()
        if self.worker and self.worker.is_alive():
            self.worker.join()
        with self.lock:
            self.pending.clear()

    def suppress(self, path, seconds=30):
        with self.lock:
            self.suppressed[str(path).casefold()] = time.monotonic() + seconds

    def enqueue(self, name, folder):
        if not self.stop_event.is_set() and not ignored(Path(name).name, self.settings):
            with self.lock:
                self.pending[(str(Path(name).absolute()), folder)] = time.monotonic()

    def _work(self):
        delay = max(.1, float(self.settings.get("debounce_seconds", 2)))
        while not self.stop_event.wait(.15):
            now = time.monotonic()
            with self.lock:
                ready = [key for key, value in self.pending.items() if now - value >= delay]
                for key in ready:
                    self.pending.pop(key, None)
            with self.lock:
                self.suppressed = {key: end for key, end in self.suppressed.items() if end > now}
            for name, folder in ready:
                if self.stop_event.is_set():
                    return
                try:
                    self._process(Path(name), folder)
                except FileNotFoundError:
                    # The file vanished mid-processing (temporary download file, already moved, ...): nothing to do.
                    continue
                except Exception as exc:
                    self.emit("خطا: " + Path(name).name + " — " + str(exc))

    def _process(self, path, folder):
        key = str(path).casefold()
        if key in self.suppressed or not path.is_file():
            return
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if self.processed.get(key) == signature:
            return
        if self.stop_event.wait(max(.05, float(self.settings.get("stable_interval", .5)))):
            return
        later = path.stat()
        if signature != (later.st_size, later.st_mtime_ns):
            self.enqueue(str(path), folder)
            return
        for rule in self.watch_map[folder]:
            if self.stop_event.is_set():
                return
            content = extract_content(path, self.settings) if (rule.get("conditions") or {}).get("content_contains") else None
            sample = Sample(path.name, stat.st_size / 1024 ** 2, content, datetime.fromtimestamp(stat.st_mtime))
            if matches(rule, sample)[0]:
                result = self.execute(rule, path)
                if result is None or result != path:
                    break
        self.processed[key] = signature
        if len(self.processed) > 10000:
            self.processed.pop(next(iter(self.processed)))

    def _record(self, kind, source, target, backup=None, size=0, recycled=False):
        if self.record:
            op = {"kind": kind, "source": str(source), "target": str(target), "backup": backup, "size": size, "mtime": 0, "recycled": recycled}
            if kind != "delete":
                stat = target.stat()
                op.update(size=stat.st_size, mtime=stat.st_mtime_ns)
            self.record(op)

    def execute(self, rule, current):
        for action in rule["actions"]:
            if self.stop_event.is_set() or not current.exists():
                break
            kind = action["type"]
            if kind == "delete":
                recycle = action.get("recycle", True)
                backup = make_backup(current) if self.record else None
                size = current.stat().st_size
                # Never permanently delete a file if the undo backup could not be made.
                if self.record and not recycle and not backup:
                    raise ValueError("حذف دائمی متوقف شد؛ تهیهٔ نسخهٔ پشتیبان ممکن نیست. فایل حذف نشد.")
                send2trash(str(current)) if recycle else current.unlink()
                # A file sent to the Recycle Bin can be restored even when no backup copy exists.
                if backup or recycle:
                    self._record("delete", current, current, backup, size, bool(recycle))
                self.emit("حذف: " + str(current))
                return None
            template = action["destination"]
            rendered = render_destination(current.name, template, rule["name"], datetime.fromtimestamp(current.stat().st_mtime))
            target = Path(rendered).expanduser().absolute()
            if target.is_dir() or template.endswith(("/", "\\")) or not target.suffix:
                target /= current.name
            if target == current or (target.exists() and os.path.samefile(str(current), str(target))):
                continue
            if not action.get("overwrite", False):
                base, index = target, 1
                while target.exists():
                    target = base.with_name(base.stem + "_" + str(index) + base.suffix)
                    index += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            self.suppressed[str(target).casefold()] = time.monotonic() + 30
            if kind == "move":
                backup = None
                if target.exists() and action.get("overwrite", False):
                    if target.is_dir():
                        raise ValueError("جایگزینی پوشه مجاز نیست")
                    backup = make_backup(target) if self.record else None
                    if self.record and not backup:
                        raise ValueError("انتقال متوقف شد؛ تهیهٔ نسخهٔ پشتیبان از فایل مقصد ممکن نیست. فایل مقصد تغییر نکرد.")
                    try:
                        os.replace(str(current), str(target))
                    except OSError as exc:
                        # Do not turn permission/locking errors into a destructive copy fallback.
                        if getattr(exc, "errno", None) != errno.EXDEV:
                            raise
                        # Cross-volume moves need a staged copy. Keep a rollback copy of the
                        # old destination until the source has been removed successfully.
                        rollback_fd, rollback_name = tempfile.mkstemp(
                            prefix=".filenazm-rollback-", dir=str(target.parent)
                        )
                        os.close(rollback_fd)
                        staged_fd, staged_name = tempfile.mkstemp(
                            prefix=".filenazm-staged-", dir=str(target.parent)
                        )
                        os.close(staged_fd)
                        try:
                            shutil.copy2(str(target), rollback_name)
                            shutil.copy2(str(current), staged_name)
                            os.replace(staged_name, str(target))
                            try:
                                current.unlink()
                            except OSError:
                                # Restore the previous destination if source removal fails.
                                os.replace(rollback_name, str(target))
                                raise
                        finally:
                            for temporary in (rollback_name, staged_name):
                                try:
                                    os.unlink(temporary)
                                except OSError:
                                    pass
                    if backup:
                        self._record("move", current, target, backup)
                else:
                    shutil.move(str(current), str(target))
                    self._record("move", current, target)
                self.emit("انتقال: " + str(current) + " → " + str(target))
                current = target
            else:
                existed = target.exists()
                backup = make_backup(target) if existed and self.record else None
                if existed and self.record and not backup:
                    raise ValueError("کپی متوقف شد؛ تهیهٔ نسخهٔ پشتیبان از فایل مقصد ممکن نیست. فایل مقصد تغییر نکرد.")
                shutil.copy2(str(current), str(target))
                if backup or not existed:
                    self._record("copy", current, target, backup)
                self.emit("کپی: " + str(current) + " → " + str(target))
        return current


def _restore_backup(op, destination):
    backup = op.get("backup")
    if backup and Path(backup).is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(backup, str(destination))
    discard_backup(op)


def undo_operation(op, engine=None):
    """Reverse one recorded real operation. Returns a log message; raises ValueError when it is no longer safe."""
    source, target = Path(op["source"]), Path(op["target"])
    if op["kind"] == "delete":
        if source.exists():
            raise ValueError("در مسیر اصلی فایل دیگری هست: " + str(source))
        if engine:
            engine.suppress(source)
        if op.get("recycled") and restore_from_recycle_bin(source, op.get("size")):
            discard_backup(op)
            return "فایل از سطل بازیافت برگشت: " + str(source)
        if not op.get("backup") or not Path(op["backup"]).is_file():
            raise ValueError("فایل در سطل بازیافت پیدا نشد (شاید سطل خالی شده یا فایل از آن بیرون رفته است): " + str(source))
        _restore_backup(op, source)
        return "فایل حذف‌شده برگشت: " + str(source)
    if not target.is_file():
        raise ValueError("فایل دیگر در مقصد نیست: " + str(target))
    if op["kind"] == "move":
        if source.exists():
            raise ValueError("در مبدأ فایل دیگری هست: " + str(source))
        source.parent.mkdir(parents=True, exist_ok=True)
        if engine:
            engine.suppress(source)
        shutil.move(str(target), str(source))
        _restore_backup(op, target)
        return "فایل به جای اول برگشت: " + str(source)
    stat = target.stat()
    if (stat.st_size, stat.st_mtime_ns) != (op["size"], op["mtime"]):
        raise ValueError("نسخهٔ کپی‌شده بعداً تغییر کرده و پاک نشد: " + str(target))
    if op.get("backup"):
        shutil.copy2(op["backup"], str(target))
        discard_backup(op)
        return "فایل جایگزین‌شده به حالت قبل برگشت: " + str(target)
    send2trash(str(target))
    return "نسخهٔ کپی‌شده به سطل بازیافت رفت: " + str(target)
