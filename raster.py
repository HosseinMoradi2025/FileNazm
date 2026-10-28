"""Internal RGB raster, scanline shapes, GIF LZW and Win32 halftone scaling.

Only the small API used by FileNazm is implemented; this is not a Pillow shim.
"""
import ctypes as C
from ctypes import wintypes as W
from collections import Counter
import math
import struct
import tkinter as tk
from native_files import _dll, _fn


def color(value):
    if isinstance(value, str):
        value = {'white': '#ffffff', 'black': '#000000'}.get(value, value)
        if len(value) != 7 or not value.startswith('#'): raise ValueError('Expected RGB color')
        return bytes.fromhex(value[1:])
    return bytes(value[:3])


class Raster:
    def __init__(self, size, data):
        self.size = self.width, self.height = size
        if self.width < 1 or self.height < 1 or self.width * self.height > 16000000: raise ValueError('Raster size limit')
        if len(data) != self.width * self.height * 3: raise ValueError('RGB length mismatch')
        self.data = bytearray(data)

    def paste(self, fill, box, mask):
        x, y, right, bottom = box; rgb = color(fill); width = right - x
        for row in range(max(0, y), min(self.height, bottom)):
            for col in range(max(0, x), min(self.width, right)):
                alpha = mask[(row - y) * width + col - x]
                if not alpha: continue
                offset = (row * self.width + col) * 3
                if alpha == 255: self.data[offset:offset+3] = rgb
                else:
                    for channel in range(3):
                        self.data[offset+channel] = (rgb[channel] * alpha + self.data[offset+channel] * (255-alpha) + 127) // 255

    def resize(self, size, resample=None):
        width, height = size
        if size == self.size: return self
        # HALFTONE uses native GDI to keep responsive playback at arbitrary sizes.
        g = _dll('gdi32')
        class HEADER(C.Structure):
            _fields_ = [('size', W.DWORD), ('width', W.LONG), ('height', W.LONG), ('planes', W.WORD), ('bits', W.WORD), ('compression', W.DWORD), ('image_size', W.DWORD), ('x', W.LONG), ('y', W.LONG), ('used', W.DWORD), ('important', W.DWORD)]
        create_dc = _fn(g, 'CreateCompatibleDC', [W.HDC], W.HDC)
        create_dib = _fn(g, 'CreateDIBSection', [W.HDC, C.POINTER(HEADER), W.UINT, C.POINTER(W.LPVOID), W.HANDLE, W.DWORD], W.HBITMAP)
        select = _fn(g, 'SelectObject', [W.HDC, W.HANDLE], W.HANDLE)
        dc = create_dc(None); bitmap = old = None
        try:
            if not dc: raise C.WinError(C.get_last_error())
            info = HEADER(40, width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0)
            bits = W.LPVOID(); bitmap = create_dib(dc, C.byref(info), 0, C.byref(bits), None, 0)
            if not bitmap: raise C.WinError(C.get_last_error())
            old = select(dc, bitmap)
            _fn(g, 'SetStretchBltMode', [W.HDC, C.c_int], C.c_int)(dc, 4)
            _fn(g, 'SetBrushOrgEx', [W.HDC, C.c_int, C.c_int, C.POINTER(W.POINT)], W.BOOL)(dc, 0, 0, None)
            source = bytearray(self.width*self.height*4)
            source[0::4], source[1::4], source[2::4] = self.data[2::3], self.data[1::3], self.data[0::3]
            src_info = HEADER(40, self.width, -self.height, 1, 32, 0, len(source), 0, 0, 0, 0)
            buf = (C.c_char * len(source)).from_buffer(source)
            result = _fn(g, 'StretchDIBits', [W.HDC] + [C.c_int]*8 + [W.LPVOID, C.POINTER(HEADER), W.UINT, W.DWORD], C.c_int)(dc, 0, 0, width, height, 0, 0, self.width, self.height, buf, C.byref(src_info), 0, 0x00CC0020)
            if result in (0, -1): raise C.WinError(C.get_last_error())
            _fn(g, 'GdiFlush', [], W.BOOL)()
            raw = C.string_at(bits, width*height*4); rgb = bytearray(width*height*3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            return Raster(size, rgb)
        finally:
            if old: select(dc, old)
            if bitmap: _fn(g, 'DeleteObject', [W.HANDLE], W.BOOL)(bitmap)
            if dc: _fn(g, 'DeleteDC', [W.HDC], W.BOOL)(dc)

    def quantize(self, colors=96):
        pixels = Counter(item[0] for item in struct.iter_unpack('3s', self.data))
        palette = [rgb for rgb, _ in pixels.most_common(min(256, colors))]
        lookup = {rgb: bytes([index]) for index, rgb in enumerate(palette)}
        for rgb in pixels.keys() - lookup.keys():
            best = min(range(len(palette)), key=lambda i: sum((rgb[c] - palette[i][c])**2 for c in range(3)))
            lookup[rgb] = bytes([best])
        indices = b''.join(lookup[item[0]] for item in struct.iter_unpack('3s', self.data))
        return Indexed(self.size, palette, indices)


class Draw:
    def __init__(self, image): self.image = image

    def span(self, x1, x2, y, fill):
        im = self.image
        if y < 0 or y >= im.height: return
        x1, x2 = max(0, int(x1)), min(im.width-1, int(x2))
        if x2 < x1: return
        start = (y*im.width+x1)*3
        im.data[start:start+(x2-x1+1)*3] = color(fill)*(x2-x1+1)

    def rectangle(self, box, fill):
        x1, y1, x2, y2 = map(int, box)
        for y in range(y1, y2+1): self.span(x1, x2, y, fill)

    def rounded_rectangle(self, box, radius=0, fill=None, outline=None, width=1):
        x1, y1, x2, y2 = map(int, box)
        radius = max(0, min(radius, (x2-x1)/2, (y2-y1)/2))
        def solid(rect, r, ink):
            a, b, c, d = rect
            for y in range(b, d+1):
                dy = max(b+r-y, y-(d-r), 0)
                inset = int(round(r-math.sqrt(max(0, r*r-dy*dy)))) if r else 0
                self.span(a+inset, c-inset, y, ink)
        if outline:
            solid((x1,y1,x2,y2), radius, outline)
            if fill and x2-x1 >= 2*width and y2-y1 >= 2*width:
                solid((x1+width,y1+width,x2-width,y2-width), max(0,radius-width), fill)
        elif fill: solid((x1,y1,x2,y2), radius, fill)

    def line(self, box, fill, width=1):
        x, y, x2, y2 = map(int, box); dx, dy = abs(x2-x), -abs(y2-y)
        sx, sy = (1 if x < x2 else -1), (1 if y < y2 else -1); error = dx+dy
        while True:
            self.rectangle((x-width//2, y-width//2, x+(width-1)//2, y+(width-1)//2), fill)
            if x == x2 and y == y2: break
            e = 2*error
            if e >= dy: error += dy; x += sx
            if e <= dx: error += dx; y += sy

    def polygon(self, points, fill):
        points = list(points)
        for y in range(min(p[1] for p in points), max(p[1] for p in points)+1):
            crossings = []
            for a, b in zip(points, points[1:]+points[:1]):
                if a[1] != b[1] and min(a[1],b[1]) <= y < max(a[1],b[1]):
                    crossings.append(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
            crossings.sort()
            for left, right in zip(crossings[::2], crossings[1::2]): self.span(math.ceil(left), math.floor(right), y, fill)
        for a, b in zip(points, points[1:]+points[:1]): self.line((*a,*b), fill)


def lzw_encode(data, minimum=8):
    clear, stop = 1 << minimum, (1 << minimum) + 1
    table = {}; next_code = stop+1; width = minimum+1
    output = bytearray(); bits = accumulator = 0
    def emit(code, nbits):
        nonlocal bits, accumulator
        accumulator |= code << bits; bits += nbits
        while bits >= 8:
            output.append(accumulator & 255); accumulator >>= 8; bits -= 8
    emit(clear, width)
    previous = None
    for byte in data:
        if previous is None: previous = byte; continue
        key = (previous, byte)
        if key in table: previous = table[key]; continue
        emit(previous, width)
        # Encoder dictionary is one entry ahead of the decoder.
        if next_code == (1 << width) and width < 12: width += 1
        if next_code < 4096:
            table[key] = next_code; next_code += 1
        else:
            emit(clear, width); table.clear(); next_code = stop+1; width = minimum+1
        previous = byte
    if previous is not None:
        emit(previous, width)
        if next_code == (1 << width) and width < 12: width += 1
    emit(stop, width)
    if bits: output.append(accumulator & 255)
    return bytes(output)


def lzw_decode(raw, minimum, expected):
    if not 2 <= minimum <= 8: raise ValueError('Invalid GIF LZW code size')
    clear, stop = 1 << minimum, (1 << minimum)+1
    table = []; width = minimum+1; previous = None; pos = 0; out = bytearray()
    while pos+width <= len(raw)*8:
        index, shift = divmod(pos, 8)
        code = (int.from_bytes(raw[index:index+3], 'little') >> shift) & ((1<<width)-1); pos += width
        if code == clear:
            table = [bytes([i]) for i in range(clear)] + [None, None]; width = minimum+1; previous = None; continue
        if code == stop:
            if len(out) != expected: raise ValueError('GIF frame length mismatch')
            return bytes(out)
        if not table: raise ValueError('GIF missing clear code')
        if code < len(table): entry = table[code]
        elif code == len(table) and previous is not None: entry = previous+previous[:1]
        else: raise ValueError('Invalid GIF LZW code')
        if entry is None: raise ValueError('Invalid GIF LZW entry')
        out.extend(entry)
        if len(out) > expected: raise ValueError('GIF decompression limit')
        if previous is not None and len(table) < 4096:
            table.append(previous+entry[:1])
            if len(table) == (1<<width) and width < 12: width += 1
        previous = entry
    raise ValueError('Truncated GIF')


class Indexed:
    def __init__(self, size, palette, indices): self.size, self.palette, self.indices = size, palette, indices

    def save(self, output, format='GIF', save_all=True, append_images=(), duration=(), loop=0, disposal=2, optimize=False):
        w, h = self.size
        output.write(b'GIF89a'+struct.pack('<HHBBB',w,h,0x70,0,0))
        output.write(b'!\xff\x0bNETSCAPE2.0\x03\x01'+struct.pack('<H',loop)+b'\0')
        for frame, delay in zip([self]+list(append_images), duration):
            if frame.size != self.size: raise ValueError('GIF frame sizes differ')
            output.write(b'!\xf9\x04'+bytes([disposal<<2])+struct.pack('<H',max(1,delay//10))+b'\0\0')
            output.write(b','+struct.pack('<HHHHB',0,0,w,h,0x87))
            output.write(b''.join(frame.palette).ljust(768,b'\0'))
            encoded = lzw_encode(frame.indices)
            output.write(b'\x08')
            for i in range(0,len(encoded),255):
                chunk = encoded[i:i+255]; output.write(bytes([len(chunk)])+chunk)
            output.write(b'\0')
        output.write(b';')


class Gif:
    """Reads the full-frame GIF89a dialect emitted above, lazily decodes frames."""
    def __init__(self, stream):
        raw = stream.read(); self.frames = []; self.index = 0; self.info = {}
        if raw[:6] != b'GIF89a' or len(raw) < 13: raise ValueError('Invalid GIF')
        w,h,flags,_,_ = struct.unpack_from('<HHBBB',raw,6); self.size = w,h
        if not w or not h or w*h > 16000000: raise ValueError('GIF size limit')
        pos = 13; global_palette = None; delay = 90
        if flags & 128:
            length = 3*(2 << (flags & 7)); global_palette = raw[pos:pos+length]; pos += length
        def subblocks():
            nonlocal pos
            chunks = []
            while True:
                length = raw[pos]; pos += 1
                if not length: return b''.join(chunks)
                if pos+length > len(raw): raise ValueError('Truncated GIF subblock')
                chunks.append(raw[pos:pos+length]); pos += length
        while pos < len(raw):
            marker = raw[pos]; pos += 1
            if marker == 59: break
            if marker == 33:
                kind = raw[pos]; pos += 1
                data = subblocks()
                if kind == 249:
                    if len(data) != 4 or data[0]&1: raise ValueError('Unsupported GIF control')
                    delay = struct.unpack_from('<H',data,1)[0]*10
            elif marker == 44:
                x,y,fw,fh,flags = struct.unpack_from('<HHHHB',raw,pos); pos += 9
                if (x,y,fw,fh) != (0,0,w,h) or flags&64: raise ValueError('Unsupported GIF frame layout')
                palette = global_palette
                if flags&128:
                    length = 3*(2 << (flags&7)); palette = raw[pos:pos+length]; pos += length
                if not palette: raise ValueError('Missing GIF palette')
                minimum = raw[pos]; pos += 1
                self.frames.append((palette,minimum,subblocks(),delay))
            else: raise ValueError('Unexpected GIF marker')
        self.n_frames = len(self.frames)
        if not self.n_frames: raise ValueError('Empty GIF')
        self.seek(0)

    def seek(self, index):
        if not 0 <= index < self.n_frames: raise EOFError()
        self.index = index; self.info['duration'] = self.frames[index][3]

    def convert(self, mode):
        palette, minimum, raw, _ = self.frames[self.index]
        indices = lzw_decode(raw,minimum,self.size[0]*self.size[1])
        colors = [palette[i:i+3] for i in range(0,len(palette),3)]
        return Raster(self.size,b''.join(colors[index] for index in indices))

    def close(self): self.frames.clear()


class Image:
    class Resampling: LANCZOS = 1  # caller compatibility; actual scaler is GDI HALFTONE
    @staticmethod
    def new(mode,size,fill):
        if mode != 'RGB': raise ValueError('Only RGB surfaces are supported')
        return Raster(size,color(fill)*(size[0]*size[1]))
    open = Gif


class ImageDraw:
    Draw = Draw


class ImageTk:
    @staticmethod
    def PhotoImage(image,master=None):
        data = ('P6\n%d %d\n255\n' % image.size).encode('ascii')+bytes(image.data)
        return tk.PhotoImage(data=data,format='PPM',master=master)
