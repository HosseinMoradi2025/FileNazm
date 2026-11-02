"""Batch storyboard matching the reference video; produces a real GIF in RAM."""
from io import BytesIO
from pathlib import PureWindowsPath
from raster import Image, ImageDraw
from drawing import label, file_icon

LIGHT = dict(bg="#f1f7f3", card="#ffffff", ink="#163326", muted="#6f8a7b", line="#dfeae3", blue="#008f47", green="#16866b", red="#d35367", active="#e0f5e8", soft="#f5faf7")
DARK = dict(bg="#0f1a16", card="#172720", ink="#eef8f2", muted="#a3bdb1", line="#2d4339", blue="#1fae63", green="#83d89e", red="#ff8896", active="#1f4535", soft="#1c3028")


def snapshot(samples, steps, completed):
    sources = ["waiting"] * len(samples)
    results = []
    for step in steps[:completed]:
        if step.action == "skip":
            if sources[step.sample_index] == "waiting":
                sources[step.sample_index] = "skip"
        else:
            if step.action in ("move", "delete"):
                sources[step.sample_index] = step.action
                results = [item for item in results if item.destination.casefold() != step.source.casefold()]
            elif sources[step.sample_index] == "waiting":
                sources[step.sample_index] = "copy"
            # overwrite replaces a virtual destination, just as the real operation does.
            results = [item for item in results if item.destination.casefold() != step.destination.casefold() or step.action == "delete"]
            results.append(step)
    return sources, results


def folder_of(step):
    """Destination folder of a step: (short name, full path); delete steps have no folder."""
    if step.action == "delete":
        return step.destination, step.destination
    parent = PureWindowsPath(step.destination).parent
    return (parent.name or str(parent)), str(parent)


def target_title(steps):
    """Name of the destination folder(s) for the result-card header."""
    names = list(dict.fromkeys(folder_of(s)[0] for s in steps if s.action in ("move", "copy")))
    if not names:
        return "فایل‌های پردازش‌شده"
    return "پوشهٔ مقصد: " + names[0] if len(names) == 1 else "پوشهٔ مقصد: {} و {} پوشهٔ دیگر".format(names[0], len(names) - 1)


def summary(steps):
    changed = {step.sample_index for step in steps if step.action != "skip"}
    all_files = {step.sample_index for step in steps}
    return len(changed), len(all_files - changed)


def render_frame(rule, samples, steps, completed, progress=0, dark=False, font_size=11):
    c = DARK if dark else LIGHT
    image = Image.new("RGB", (1000, 550), c["bg"])
    draw = ImageDraw.Draw(image)
    size = max(16, min(24, font_size + 7))
    active = steps[completed] if completed < len(steps) else None
    states, results = snapshot(samples, steps, completed)
    for x in (24, 590):
        draw.rounded_rectangle((x, 64, x + 386, 486), radius=18, fill=c["card"], outline=c["line"], width=1)
    label(image, "پوشهٔ مبدأ • فایل‌های خیالی", (42, 16, 350, 32), c["ink"], size, True, "right")
    label(image, "نتیجهٔ اجرای قانون", (608, 16, 350, 32), c["ink"], size, True, "right")
    label(image, rule["watch"], (44, 76, 346, 32), c["muted"], 16)
    label(image, target_title(steps), (610, 76, 344, 32), c["green"], 17, True, "right")
    draw.line((44, 116, 390, 116), fill=c["line"])
    draw.line((610, 116, 956, 116), fill=c["line"])
    for i, sample in enumerate(samples):
        y = 132 + i * 55
        selected = active is not None and active.sample_index == i
        gone = states[i] in ("move", "delete")
        bg = c["active"] if selected else c["soft"]
        draw.rounded_rectangle((42, y, 392, y + 46), radius=9, fill=bg, outline=c["blue"] if selected else bg)
        icon_color = c["muted"] if gone or states[i] == "skip" else c["blue"]
        file_icon(draw, 54, y + 8, icon_color, True)
        label(image, sample.name, (88, y + 3, 290, 25), c["muted"] if gone else c["ink"], size)
        status = {"waiting": "در انتظار بررسی", "move": "منتقل شد", "copy": "اصل فایل باقی می‌ماند", "delete": "حذف شد", "skip": "بدون تغییر"}[states[i]]
        if selected:
            status = active.reason
            if active.action in ("move", "copy"):
                status += " به «" + folder_of(active)[0] + "»"
        label(image, status, (88, y + 25, 290, 19), c["blue"] if selected else c["muted"], 13, False, "right")
    visible_results = results[-6:]
    for row, step in enumerate(visible_results):
        y = 132 + row * 55
        color = c["red"] if step.action == "delete" else c["green"]
        draw.rounded_rectangle((608, y, 958, y + 46), radius=9, fill=c["soft"])
        file_icon(draw, 620, y + 8, color, True)
        name = PureWindowsPath(step.source if step.action == "delete" else step.destination).name
        label(image, name, (654, y + 2, 290, 25), c["ink"], size)
        short, full = folder_of(step)
        label(image, ("در پوشهٔ " if step.action != "delete" else "") + short, (790, y + 25, 154, 19), color, 14, True, "right")
        label(image, full, (654, y + 25, 132, 19), c["muted"], 12, False, "left")
    if not results:
        label(image, "نتیجه اینجا نمایش داده می‌شود", (622, 276, 320, 36), c["muted"], 17, False, "center")
    draw.line((430, 268, 570, 268), fill=c["line"], width=3)
    draw.polygon(((570, 268), (560, 260), (560, 276)), fill=c["line"])
    if active:
        label(image, active.reason, (416, 210, 168, 40), c["blue"] if active.action != "skip" else c["muted"], 16, True, "center")
        if active.action in ("move", "copy"):
            label(image, "«" + folder_of(active)[0] + "»", (416, 282, 168, 36), c["green"], 17, True, "center")
        if active.action != "skip":
            t = min(1, max(0, (progress - .18) / .65))
            t = t * t * (3 - 2 * t)
            sy = 140 + active.sample_index * 55
            dy = 140 + min(len(results), 5) * 55
            x, y = int(55 + 565 * t), int(sy + (dy - sy) * t - 32 * (4 * t * (1 - t)))
            draw.rounded_rectangle((x - 5, y - 4, x + 39, y + 45), radius=8, fill=c["active"])
            file_icon(draw, x, y, c["red"] if active.action == "delete" else c["blue"])
    else:
        changed, unchanged = summary(steps)
        label(image, "پایان", (416, 226, 168, 42), c["green"], 22, True, "center")
        label(image, "{} فایل تغییر کرد • {} فایل بدون تغییر".format(changed, unchanged), (48, 497, 904, 33), c["green"], size, True, "center")
    if active:
        label(image, "بررسی فایل {} از {}  •  {}".format(active.sample_index + 1, len(samples), samples[active.sample_index].name), (48, 497, 904, 33), c["muted"], 17, False, "center")
    ratio = (completed + progress) / max(1, len(steps))
    draw.rounded_rectangle((24, 541, 976, 546), radius=2, fill=c["line"])
    if ratio > 0:
        draw.rounded_rectangle((24, 541, 24 + int(952 * min(1, ratio)), 546), radius=2, fill=c["blue"])
    return image


def make_gif(rule, samples, steps, dark=False, font_size=11, cancel=None):
    frames, durations = [], []
    # Indexed frames keep RAM bounded; no temp files, exports or disk cache.
    per_step = max(4, min(10, 150 // max(1, len(steps))))
    for completed, step in enumerate(steps):
        count = 4 if step.action == "skip" else per_step
        for index in range(count):
            if cancel and cancel.is_set():
                return None
            frame = render_frame(rule, samples, steps, completed, index / max(1, count - 1), dark, font_size)
            frames.append(frame.quantize(colors=96))
            durations.append(90 if step.action != "skip" else 120)
    final = render_frame(rule, samples, steps, len(steps), 0, dark, font_size).quantize(colors=96)
    frames.append(final)
    durations.append(1400)
    output = BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, disposal=2, optimize=False)
    return output.getvalue()
