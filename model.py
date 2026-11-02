"""Pure rule evaluation, virtual batch simulation and undo history; Python 3.8."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
import fnmatch
import math
import re
import unicodedata

VERSION = "1.1.0"
ACTIONS = {"move": "انتقال", "copy": "کپی", "delete": "حذف"}
IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
IGNORED_SUFFIXES = (".tmp", ".temp", ".part", ".crdownload", ".download", ".partial")


@dataclass(frozen=True)
class Sample:
    name: str
    size_mb: float = 1.0
    content: str = ""
    modified: datetime = datetime(2026, 10, 22, 12, 0)


@dataclass(frozen=True)
class Step:
    sample_index: int
    action: str
    source: str
    destination: str
    reason: str


def extensions(text):
    return list(dict.fromkeys("." + x.strip().lower().lstrip(".") for x in text.replace("،", ",").split(",") if x.strip()))


def ignored(name, settings):
    name = name.lower()
    return name in IGNORED_NAMES or name.startswith("~$") or name.endswith(IGNORED_SUFFIXES) or (settings.get("ignore_hidden", True) and name.startswith("."))


def matches(rule, sample):
    c = rule.get("conditions") or {}
    if c.get("extensions") and PureWindowsPath(sample.name).suffix.lower() not in {"." + x.lower().lstrip(".") for x in c["extensions"]}:
        return False, "پسوند مطابقت ندارد"
    if c.get("filename_globs") and not any(fnmatch.fnmatchcase(sample.name.lower(), p.lower()) for p in c["filename_globs"]):
        return False, "نام فایل مطابقت ندارد"
    if c.get("min_size_mb") is not None and sample.size_mb < c["min_size_mb"]:
        return False, "حجم کمتر از حداقل است"
    if c.get("max_size_mb") is not None and sample.size_mb > c["max_size_mb"]:
        return False, "حجم بیشتر از حداکثر است"
    terms = c.get("content_contains") or []
    terms = [terms] if isinstance(terms, str) else terms
    if terms and (sample.content is None or not all(str(t).lower() in sample.content.lower() for t in terms)):
        return False, "محتوا مطابقت ندارد"
    return True, "مطابق قانون"


def validate(rule):
    if not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or not rule["name"].strip() or not isinstance(rule.get("watch"), str) or not rule["watch"].strip():
        raise ValueError("نام قانون و پوشهٔ نظارت الزامی است")
    c = rule.get("conditions") or {}
    if not isinstance(c, dict):
        raise ValueError("شرایط قانون معتبر نیست")
    unknown = set(c) - {"extensions", "filename_globs", "min_size_mb", "max_size_mb", "content_contains"}
    if unknown:
        raise ValueError("شرط پشتیبانی‌نشده: " + ", ".join(unknown))
    for key in ("extensions", "filename_globs"):
        if c.get(key) is not None and (not isinstance(c[key], list) or not all(isinstance(x, str) and x for x in c[key])):
            raise ValueError("پسوندها و الگوها باید فهرستی از متن باشند")
    for key in ("min_size_mb", "max_size_mb"):
        v = c.get(key)
        if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
            raise ValueError("حجم باید عدد نامنفی و متناهی باشد")
    low, high = c.get("min_size_mb"), c.get("max_size_mb")
    if low is not None and high is not None and low > high:
        raise ValueError("حداقل حجم بیشتر از حداکثر است")
    terms = c.get("content_contains")
    if terms is not None and not isinstance(terms, str) and not (isinstance(terms, list) and all(isinstance(t, str) for t in terms)):
        raise ValueError("عبارت‌های محتوا معتبر نیست")
    actions = rule.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("حداقل یک عملیات لازم است")
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in ACTIONS:
            raise ValueError("عملیات معتبر نیست")
        if action["type"] != "delete":
            dest = action.get("destination")
            if not isinstance(dest, str) or not dest.strip():
                raise ValueError("مقصد عملیات الزامی است")
            try:
                render_destination("sample.txt", dest, rule["name"], datetime(2026, 10, 22))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("قالب مقصد معتبر نیست") from exc


def render_destination(name, template, rule_name, modified):
    p = PureWindowsPath(name)
    slug = unicodedata.normalize("NFKD", rule_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-").lower() or "rule"
    class Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    return template.format_map(Safe(name=p.stem, filename=p.stem, ext=p.suffix.lstrip(".").lower(), date=modified.strftime("%Y-%m-%d"), year=modified.strftime("%Y"), month=modified.strftime("%m"), rule=slug))


def make_samples(rule):
    """Six deterministic imaginary files; a non-match is always labelled honestly."""
    c = rule.get("conditions") or {}
    exts = c.get("extensions") or [".jpg", ".png"]
    exts = ["." + ext.lstrip(".") for ext in exts]
    names = ["sample1" + exts[0], "sample2" + exts[min(1, len(exts) - 1)]]
    if c.get("filename_globs"):
        pattern = c["filename_globs"][0]
        generated = re.sub(r"\[([^\]]+)\]", lambda m: ("z" if m[1].startswith("!") else m[1][0]), pattern)
        names[0] = generated.replace("*", "sample1").replace("?", "1")
        names[1] = generated.replace("*", "sample2").replace("?", "2")
    names += ["report.pdf", "notes.txt", "music.mp3", "archive.zip"]
    # Avoid duplicate virtual names even for an exact filename glob.
    seen = set()
    for i, name in enumerate(names):
        if name.casefold() in seen:
            p = PureWindowsPath(name)
            name = p.stem + "_" + str(i + 1) + p.suffix
        seen.add(name.casefold())
        names[i] = name
    minimum = c.get("min_size_mb") or 0
    maximum = c.get("max_size_mb")
    size = max(1.0, minimum) if maximum is None else (minimum + maximum) / 2
    terms = c.get("content_contains") or []
    content = terms if isinstance(terms, str) else " ".join(terms)
    return [Sample(name, size if i < 2 else 1.0, content if i < 2 else "متن نمونه") for i, name in enumerate(names)]


def simulate(rule, samples, settings=None):
    """Virtual paths only: no exists/stat/open, no calls into the real engine."""
    validate(rule)
    settings = settings or {}
    paths = [str(PureWindowsPath(rule["watch"]) / sample.name) for sample in samples]
    occupied = {p.casefold() for p in paths}
    steps = []
    for index, sample in enumerate(samples):
        source = paths[index]
        match, reason = matches(rule, sample)
        if not rule.get("enabled", True):
            match, reason = False, "قانون غیرفعال است"
        elif ignored(sample.name, settings):
            match, reason = False, "فایل نادیده گرفته می‌شود"
        if not match:
            steps.append(Step(index, "skip", source, source, reason))
            continue
        for action in rule["actions"]:
            kind = action["type"]
            if kind == "delete":
                target = "سطل بازیافت" if action.get("recycle", True) else "حذف دائمی"
            else:
                template = action["destination"]
                path = PureWindowsPath(render_destination(PureWindowsPath(source).name, template, rule["name"], sample.modified))
                if template.endswith(("/", "\\")) or not path.suffix:
                    path /= PureWindowsPath(source).name
                target = str(path)
                if target.casefold() == source.casefold():
                    steps.append(Step(index, "skip", source, source, "مبدأ و مقصد یکسان است"))
                    continue
                if not action.get("overwrite", False):
                    n = 1
                    while target.casefold() in occupied:
                        target = str(path.with_name(path.stem + "_" + str(n) + path.suffix))
                        n += 1
                occupied.add(target.casefold())
            steps.append(Step(index, kind, source, target, ACTIONS[kind]))
            if kind in ("move", "delete"):
                occupied.discard(source.casefold())
            if kind == "move":
                source = target
            if kind == "delete":
                break
    return steps


class History:
    """Undo/redo of rule-list changes. Each entry keeps the rules, a short label and the selected row."""
    LIMIT = 100

    def __init__(self):
        self.undo_stack, self.redo_stack = [], []

    def record(self, rules, label="تغییر قانون‌ها", index=None):
        self.undo_stack.append(dict(rules=deepcopy(rules), label=label, index=index))
        self.undo_stack = self.undo_stack[-self.LIMIT:]
        self.redo_stack.clear()

    def next_label(self, redo=False):
        stack = self.redo_stack if redo else self.undo_stack
        return stack[-1]["label"] if stack else None

    def restore(self, current, redo=False):
        """Returns the entry to apply (rules/label/index) or None; the opposite stack receives the current state."""
        source, destination = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            return None
        entry = source.pop()
        destination.append(dict(rules=deepcopy(current), label=entry["label"], index=entry["index"]))
        return entry
