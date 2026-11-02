"""Bounded DOCX and PDF text extraction. Unsupported PDF encodings fail closed.

This is a text reader, not a renderer/OCR/encryption implementation. PDF object
syntax is tokenized; regular expressions are used only for lexical boundaries.
"""
import base64
import re
import zlib
import zipfile
import xml.etree.ElementTree as ET

LIMIT = 64 * 1024 * 1024


class UnsupportedPDF(ValueError):
    pass


def docx_text(path):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('word/document.xml')
        if info.file_size > LIMIT: raise ValueError('DOCX XML exceeds safety limit')
        raw = archive.read(info)
    check = raw.replace(b'\0', b'')  # also detect declarations in UTF-16/32 XML
    if b'<!DOCTYPE' in check or b'<!ENTITY' in check: raise ValueError('XML entities are not permitted')
    root = ET.fromstring(raw)
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    body = root.find(ns + 'body')
    if body is None: raise ValueError('DOCX body missing')
    # Match Document.paragraphs: direct body paragraphs, not table/header text.
    paragraphs = []
    for paragraph in body.findall(ns + 'p'):
        text = []
        runs = []
        for child in paragraph:
            if child.tag == ns + 'r': runs.append(child)
            elif child.tag == ns + 'hyperlink': runs.extend(child.findall(ns + 'r'))
        for run in runs:
            for item in run:
                if item.tag == ns + 't': text.append(item.text or '')
                elif item.tag == ns + 'tab': text.append('\t')
                elif item.tag in (ns + 'br', ns + 'cr'): text.append('\n')
        paragraphs.append(''.join(text))
    return '\n'.join(paragraphs)


class Name(str): pass
class Ref(tuple): pass


class Syntax:
    def __init__(self, data): self.data, self.pos = data, 0

    def skip(self):
        while self.pos < len(self.data):
            c = self.data[self.pos]
            if c in b'\0\t\n\f\r ': self.pos += 1
            elif c == 37:
                end = self.data.find(b'\n', self.pos)
                self.pos = len(self.data) if end < 0 else end + 1
            else: break

    def value(self, depth=0):
        if depth > 64: raise UnsupportedPDF('PDF nesting limit')
        self.skip(); data = self.data; start = self.pos
        if start >= len(data): raise EOFError()
        if data.startswith(b'<<', start):
            self.pos += 2; result = {}
            while True:
                self.skip()
                if data.startswith(b'>>', self.pos): self.pos += 2; return result
                key = self.value(depth + 1)
                if not isinstance(key, Name): raise UnsupportedPDF('Invalid PDF dictionary')
                result[str(key)] = self.value(depth + 1)
        c = data[start]; self.pos += 1
        if c == 91:
            result = []
            while True:
                self.skip()
                if self.pos >= len(data): raise UnsupportedPDF('Unterminated PDF array')
                if data[self.pos] == 93: self.pos += 1; return result
                result.append(self.value(depth + 1))
        if c == 40:
            result = bytearray(); level = 1
            while self.pos < len(data):
                c = data[self.pos]; self.pos += 1
                if c == 92:
                    if self.pos >= len(data): break
                    c = data[self.pos]; self.pos += 1
                    if c in b'01234567':
                        digits = bytes([c])
                        for _ in range(2):
                            if self.pos < len(data) and data[self.pos] in b'01234567':
                                digits += data[self.pos:self.pos+1]; self.pos += 1
                            else: break
                        result.append(int(digits, 8) & 255)
                    elif c == 13:
                        if data[self.pos:self.pos+1] == b'\n': self.pos += 1
                    elif c != 10: result.append({110:10, 114:13, 116:9, 98:8, 102:12}.get(c, c))
                else:
                    if c == 40: level += 1
                    if c == 41:
                        level -= 1
                        if not level: return bytes(result)
                    result.append(c)
            raise UnsupportedPDF('Unterminated PDF literal string')
        if c == 60:
            end = data.find(b'>', self.pos)
            if end < 0: raise UnsupportedPDF('Unterminated hex string')
            digits = re.sub(rb'\s+', b'', data[self.pos:end]); self.pos = end + 1
            if len(digits) % 2: digits += b'0'
            return bytes.fromhex(digits.decode('ascii'))
        match = re.match(rb'[^\x00\s()<>\[\]{}/%]+', data[self.pos:] if c == 47 else data[start:])
        if not match: raise UnsupportedPDF('Unexpected PDF delimiter')
        token = match[0]; self.pos = (start + 1 if c == 47 else start) + len(token)
        if c == 47:
            token = re.sub(rb'#([0-9a-fA-F]{2})', lambda m: bytes([int(m[1], 16)]), token)
            return Name(token.decode('latin1'))
        if re.fullmatch(rb'[-+]?\d+', token):
            value = int(token); end = self.pos
            ref = re.match(rb'\s+(\d+)\s+R\b', data[end:])
            if ref: self.pos += ref.end(); return Ref((value, int(ref[1])))
            return value
        if re.fullmatch(rb'[-+]?(?:\d+\.\d*|\.\d+)', token): return float(token)
        if token == b'true': return True
        if token == b'false': return False
        if token == b'null': return None
        return token.decode('latin1')


def inflate(raw):
    decoder = zlib.decompressobj()
    result = decoder.decompress(raw, LIMIT + 1)
    if len(result) > LIMIT or decoder.unconsumed_tail: raise UnsupportedPDF('PDF decompression limit')
    if not decoder.eof: raise UnsupportedPDF('Truncated PDF compressed stream')
    return result


class PDF:
    def __init__(self, raw):
        if len(raw) > LIMIT or not raw.startswith(b'%PDF-'): raise UnsupportedPDF('Invalid/oversized PDF')
        self.objects, self.streams = {}, {}
        position = 0
        while True:
            match = re.search(rb'(?<!\w)(\d+)\s+(\d+)\s+obj\b', raw[position:])
            if not match: break
            parser = Syntax(raw); parser.pos = position + match.end()
            key = (int(match[1]), int(match[2])); value = parser.value(); parser.skip()
            if raw.startswith(b'stream', parser.pos):
                if not isinstance(value, dict): raise UnsupportedPDF('Invalid stream object')
                start = parser.pos + 6
                if raw[start:start+2] == b'\r\n': start += 2
                elif raw[start:start+1] in (b'\n', b'\r'): start += 1
                else: raise UnsupportedPDF('Invalid stream newline')
                length = value.get('Length')
                if isinstance(length, int): end = start + length
                else:
                    end = raw.find(b'endstream', start)
                    if end < 0: raise UnsupportedPDF('Missing endstream')
                    # Indirect lengths are validated after object collection.
                self.streams[key] = (value, raw[start:end], length)
                parser.pos = end
                marker = raw.find(b'endstream', parser.pos)
                if marker < 0 or marker - parser.pos > 2: raise UnsupportedPDF('Invalid stream length')
                parser.pos = marker + 9
            end = raw.find(b'endobj', parser.pos)
            if end < 0: raise UnsupportedPDF('Missing endobj')
            self.objects[key] = value; position = end + 6
        if re.search(rb'/Encrypt\b', raw): raise UnsupportedPDF('PDF رمزگذاری‌شده پشتیبانی نمی‌شود')
        for key, value in list(self.objects.items()):
            if isinstance(value, dict) and value.get('Type') == 'ObjStm':
                stream = self.stream(Ref(key)); n, first = value.get('N'), value.get('First')
                if not isinstance(n, int) or n > 100000 or not isinstance(first, int): raise UnsupportedPDF('Invalid object stream')
                head = Syntax(stream[:first])
                entries = [(head.value(), head.value()) for _ in range(n)]
                for number, offset in entries:
                    parser = Syntax(stream); parser.pos = first + offset
                    self.objects.setdefault((number, 0), parser.value())

    def resolve(self, value):
        seen = set()
        while isinstance(value, Ref):
            if value in seen or value not in self.objects: raise UnsupportedPDF('Invalid/cyclic PDF reference')
            seen.add(value); value = self.objects[value]
        return value

    def stream(self, ref):
        if ref not in self.streams: raise UnsupportedPDF('Missing PDF stream')
        meta, raw, length = self.streams[ref]
        length = self.resolve(length)
        if not isinstance(length, int) or length < 0 or length > len(raw): raise UnsupportedPDF('Invalid PDF stream length')
        raw = raw[:length]
        filters = self.resolve(meta.get('Filter', []))
        if isinstance(filters, Name): filters = [filters]
        params = self.resolve(meta.get('DecodeParms'))
        if params and (not isinstance(params, dict) or params.get('Predictor', 1) != 1): raise UnsupportedPDF('PDF stream predictor unsupported')
        for name in filters:
            if name in ('FlateDecode', 'Fl'): raw = inflate(raw)
            elif name in ('ASCIIHexDecode', 'AHx'):
                digits = re.sub(rb'\s+', b'', raw).rstrip(b'>')
                raw = bytes.fromhex((digits + (b'0' if len(digits) % 2 else b'')).decode('ascii'))
            elif name in ('ASCII85Decode', 'A85'):
                raw = raw.rstrip()
                raw = base64.a85decode(raw[:-2] if raw.endswith(b'~>') else raw)
            else: raise UnsupportedPDF('Unsupported PDF filter: ' + str(name))
            if len(raw) > LIMIT: raise UnsupportedPDF('PDF stream exceeds limit')
        return raw

    def font_decoder(self, font):
        font = self.resolve(font)
        if not isinstance(font, dict): raise UnsupportedPDF('Missing PDF font')
        if 'ToUnicode' in font:
            raw = self.stream(font['ToUnicode']); mapping = {}
            if b'usecmap' in raw: raise UnsupportedPDF('Inherited CMap unsupported')
            for section in re.findall(rb'beginbfchar(.*?)endbfchar', raw, re.S):
                parser = Syntax(section)
                while True:
                    try: source, dest = parser.value(), parser.value()
                    except EOFError: break
                    mapping[source] = dest.decode('utf-16-be')
            for section in re.findall(rb'beginbfrange(.*?)endbfrange', raw, re.S):
                parser = Syntax(section)
                while True:
                    try: first, last, dest = parser.value(), parser.value(), parser.value()
                    except EOFError: break
                    a, b = int.from_bytes(first, 'big'), int.from_bytes(last, 'big')
                    if b - a > 65536: raise UnsupportedPDF('CMap range exceeds limit')
                    for offset, code in enumerate(range(a, b + 1)):
                        value = dest[offset] if isinstance(dest, list) else (int.from_bytes(dest, 'big') + offset).to_bytes(len(dest), 'big')
                        mapping[code.to_bytes(len(first), 'big')] = value.decode('utf-16-be')
            lengths = sorted({len(key) for key in mapping}, reverse=True)
            def decode(text):
                out, pos = [], 0
                while pos < len(text):
                    for length in lengths:
                        code = text[pos:pos+length]
                        if code in mapping:
                            out.append(mapping[code]); pos += length; break
                    else: raise UnsupportedPDF('Missing PDF Unicode glyph mapping')
                return ''.join(out)
            return decode
        encoding = self.resolve(font.get('Encoding', Name('StandardEncoding')))
        if isinstance(encoding, dict) or font.get('Subtype') == 'Type0': raise UnsupportedPDF('PDF font needs ToUnicode mapping')
        if encoding == 'WinAnsiEncoding': return lambda text: text.decode('cp1252')
        if encoding == 'MacRomanEncoding': return lambda text: text.decode('mac_roman')
        if encoding == 'StandardEncoding' and font.get('BaseFont') not in ('Symbol', 'ZapfDingbats'):
            def ascii_text(text):
                if any(c < 32 or c > 126 for c in text): raise UnsupportedPDF('Non-ASCII StandardEncoding needs a glyph map')
                return text.decode('ascii')
            return ascii_text
        raise UnsupportedPDF('Unsupported PDF font encoding')

    def content(self, raw, resources, depth=0):
        if depth > 16: raise UnsupportedPDF('Recursive PDF form')
        parser, stack, out = Syntax(raw), [], []
        font, saved = None, []
        fonts = self.resolve(resources.get('Font', {})); decoders = {}
        while True:
            try: value = parser.value()
            except EOFError: break
            if not isinstance(value, str) or isinstance(value, Name): stack.append(value); continue
            op = value
            if op == 'Tf':
                name = stack[-2]
                if name not in decoders: decoders[name] = self.font_decoder(fonts.get(name))
                font = decoders[name]
            elif op in ('Tj', 'TJ', "'", '"'):
                if font is None: raise UnsupportedPDF('PDF text without font')
                if op in ("'", '"'): out.append('\n')
                values = stack[-1] if op == 'TJ' else [stack[-1]]
                for item in values:
                    if isinstance(item, bytes): out.append(font(item))
                    elif isinstance(item, (int, float)) and item < -120: out.append(' ')
            elif op in ('Td', 'TD', 'T*', 'ET'): out.append('\n')
            elif op == 'q': saved.append(font)
            elif op == 'Q': font = saved.pop() if saved else font
            elif op == 'Do':
                ref = self.resolve(resources.get('XObject', {})).get(stack[-1])
                obj = self.resolve(ref)
                if isinstance(obj, dict) and obj.get('Subtype') == 'Form':
                    out.append(self.content(self.stream(ref), self.resolve(obj.get('Resources', resources)), depth + 1))
            elif op == 'BI': raise UnsupportedPDF('Inline PDF images unsupported in text stream')
            stack.clear()
        return ''.join(out).strip()

    def pages(self):
        catalogs = [v for v in self.objects.values() if isinstance(v, dict) and v.get('Type') == 'Catalog']
        if len(catalogs) != 1: raise UnsupportedPDF('Ambiguous PDF catalog/incremental revision')
        seen = set()
        def walk(ref, inherited, depth=0):
            if depth > 64 or ref in seen: raise UnsupportedPDF('Cyclic PDF page tree')
            seen.add(ref); page = self.resolve(ref)
            resources = self.resolve(page.get('Resources', inherited))
            if page.get('Type') == 'Page': yield page, resources
            else:
                for child in page.get('Kids', []): yield from walk(child, resources, depth + 1)
        yield from walk(catalogs[0]['Pages'], {})


def pdf_text(path, max_pages=200):
    pdf = PDF(path.read_bytes()); result = []
    for index, (page, resources) in enumerate(pdf.pages()):
        if index >= max(0, max_pages): break
        contents = page.get('Contents', [])
        if isinstance(contents, Ref) and isinstance(pdf.resolve(contents), list): contents = pdf.resolve(contents)
        if not isinstance(contents, list): contents = [contents]
        raw = b'\n'.join(pdf.stream(ref) for ref in contents)
        result.append(pdf.content(raw, resources))
    return '\n'.join(result)
