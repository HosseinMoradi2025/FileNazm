"""config_yaml.py

Safe, bounded YAML configuration dialect for FileNazm.

This module intentionally avoids all third-party libraries.
It uses only the Python standard library:
- json
- math
- re

Supported input:
- JSON
- A practical subset of YAML:
  - block mappings
  - block sequences
  - indentless sequences
  - flow mappings/lists: {} and []
  - plain scalars
  - single-quoted scalars
  - double-quoted scalars
  - comments
  - literal/folded block scalars: | and >

Rejected explicitly:
- YAML tags: !tag
- anchors: &anchor
- aliases: *alias
- merge keys: <<
- multiple documents: --- ... ---
- tabs in indentation
- non-finite floats: .inf, -.inf, +.inf, .nan

Output:
- Human-readable block YAML, not JSON.
- Strings that could be ambiguous are quoted safely.
"""

import json
import math
import re


_MAX_TEXT_CHARS = 4 * 1024 * 1024
_MAX_DEPTH = 64

_INT_RE = re.compile(r"[-+]?\d+\Z")
_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?\Z")

_NULL_WORDS = {"null", "~"}
_TRUE_WORDS = {"true", "yes", "on"}
_FALSE_WORDS = {"false", "no", "off"}
_NONFINITE_WORDS = {".inf", "-.inf", "+.inf", ".nan"}
_RESERVED_WORDS = _NULL_WORDS | _TRUE_WORDS | _FALSE_WORDS | _NONFINITE_WORDS

_SPECIAL_CHARS = set(
    ":{}[],&*#?|-<>=!%@\\`\"'\n\r\t"
)
_LEADING_SPECIAL = set(
    "-?:,[]{}#&*!|>'\"%@`"
)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _strip_comment(text):
    """
    Remove an unquoted # comment from a YAML line.

    This is deliberately simple and quote-aware. It is suitable for the
    bounded configuration dialect used by FileNazm.
    """
    quote = None
    escape = False

    for i, ch in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif ch == "\\" and quote == '"':
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()

    return text.rstrip()


def _split_top(text, delimiter):
    """
    Split text by delimiter only at top level.

    Quotes and brackets are respected. For colon, it is treated as a separator
    only when followed by whitespace or end-of-string, which avoids splitting
    values such as http://example.com.
    """
    quote = None
    escape = False
    depth = 0
    start = 0
    out = []

    for i, ch in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif ch == "\\" and quote == '"':
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == delimiter and depth == 0:
            if delimiter != ":" or i + 1 == len(text) or text[i + 1].isspace():
                out.append(text[start:i])
                start = i + 1

    out.append(text[start:])
    return out


def _parse_flow(text, depth=0):
    """Parse a YAML flow collection: [...] or {...}."""
    if depth > _MAX_DEPTH:
        raise ValueError("YAML nesting too deep")

    text = text.strip()

    def unique_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate YAML flow mapping key: " + key)
            result[key] = value
        return result

    try:
        return _validate_data(json.loads(text, object_pairs_hook=unique_pairs), depth)
    except json.JSONDecodeError:
        pass

    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        if not inner.strip():
            return []
        return [
            _parse_scalar(part, depth + 1)
            for part in _split_top(inner, ",")
            if part.strip()
        ]

    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1]
        out = {}
        if not inner.strip():
            return out

        for part in _split_top(inner, ","):
            if not part.strip():
                continue

            kv = _split_top(part, ":")
            if len(kv) != 2:
                raise ValueError("Invalid YAML flow mapping item")

            key = _parse_scalar(kv[0], depth + 1)
            if not isinstance(key, str):
                raise ValueError("YAML flow mapping keys must be strings")
            if key in out:
                raise ValueError("Duplicate YAML flow mapping key: " + key)

            out[key] = _parse_scalar(kv[1], depth + 1)

        return out

    raise ValueError("Invalid YAML flow collection")


def _parse_scalar(text, depth=0):
    """Parse a YAML scalar or flow collection into a Python object."""
    if depth > _MAX_DEPTH:
        raise ValueError("YAML nesting too deep")

    text = text.strip()

    if not text:
        return None

    if text[0] in "!&*" or text == "<<":
        raise ValueError("YAML tags/anchors/aliases/merge keys are not supported")

    if text[0] == '"':
        try:
            return json.loads(text)
        except ValueError:
            raise ValueError("Invalid double-quoted YAML string")

    if text[0] == "'":
        if len(text) < 2 or not text.endswith("'"):
            raise ValueError("Unclosed single-quoted YAML string")
        return text[1:-1].replace("''", "'")

    if text[0] in "[{":
        return _parse_flow(text, depth)

    low = text.lower()

    if low in _NONFINITE_WORDS:
        raise ValueError("Non-finite YAML numbers are not supported")

    if low in _NULL_WORDS:
        return None

    if low in _TRUE_WORDS:
        return True

    if low in _FALSE_WORDS:
        return False

    if _INT_RE.match(text):
        return int(text)

    if _FLOAT_RE.match(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Non-finite YAML number is not supported")
        return value

    return text


def _tokenize(text):
    """
    Convert YAML text into a flat token list: (indent, body).

    Block scalars are converted into JSON-encoded strings before tokenizing,
    so the later block parser only sees normal scalar values.
    """
    lines = text.splitlines()
    tokens = []
    seen_document = False
    ended_document = False
    i = 0

    while i < len(lines):
        raw = lines[i]
        i += 1

        ind = _indent(raw)

        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise ValueError("YAML indentation must use spaces")

        body = _strip_comment(raw[ind:])

        if not body:
            continue

        if body == "...":
            ended_document = True
            continue

        if ended_document:
            raise ValueError("Content after YAML document end is not supported")

        if body == "---":
            if seen_document or tokens:
                raise ValueError("Multiple YAML documents are not supported")
            seen_document = True
            continue

        # Detect literal/folded block scalar indicators at end of line.
        match = re.search(r"(?::\s*|^-\s*)([|>][-+]?)$", body)
        if match:
            indicator = match.group(1)

            if not re.fullmatch(r"[|>][-+]?", indicator):
                raise ValueError(
                    "Explicit YAML block scalar indentation is not supported"
                )

            block_lines = []

            while i < len(lines):
                nxt = lines[i]

                if not nxt.strip():
                    block_lines.append("")
                    i += 1
                    continue

                nxt_ind = _indent(nxt)
                if nxt_ind <= ind:
                    break

                block_lines.append(nxt)
                i += 1

            nonblank = [ln for ln in block_lines if ln.strip()]
            base = min((_indent(ln) for ln in nonblank), default=ind + 2)

            values = []
            for ln in block_lines:
                if ln.strip():
                    values.append(ln[base:])
                else:
                    values.append("")

            content = "\n".join(values) + ("\n" if values else "")
            style = indicator[0]
            chomp = indicator[1:]

            if style == ">":
                # Simple folded scalar handling: join wrapped non-empty lines.
                content = re.sub(r"(?<=\S)\n(?=\S)", " ", content)

            if chomp == "-":
                content = content.rstrip("\n")
            elif chomp == "+":
                pass
            else:
                content = content.rstrip("\n")
                if any(v != "" for v in values):
                    content += "\n"

            prefix = body[: match.start(1)]
            body = prefix + json.dumps(content, ensure_ascii=False)

        tokens.append((ind, body))

    return tokens


def _parse_compact_mapping(tokens, pos, parent_indent, first_body, depth):
    """
    Parse a compact mapping that starts inside a sequence item:

        - name: value
          other: value
    """
    if depth > _MAX_DEPTH:
        raise ValueError("YAML nesting too deep")

    child_indent = parent_indent + 2

    sub_tokens = [(child_indent, first_body)]

    while pos < len(tokens) and tokens[pos][0] > parent_indent:
        sub_tokens.append(tokens[pos])
        pos += 1

    result, end = _parse_block(sub_tokens, 0, child_indent, depth)
    if end != len(sub_tokens):
        raise ValueError("Invalid compact YAML mapping indentation")
    return result, pos


def _parse_block(tokens, pos, indent, depth):
    """Parse a block mapping or block sequence starting at pos."""
    if depth > _MAX_DEPTH:
        raise ValueError("YAML nesting too deep")

    if pos >= len(tokens):
        return None, pos

    first_body = tokens[pos][1]
    is_sequence = first_body == "-" or first_body.startswith("- ")

    if is_sequence:
        out = []

        while pos < len(tokens):
            ind, body = tokens[pos]

            if ind < indent:
                break

            if ind > indent:
                raise ValueError("Unexpected indentation in YAML sequence")

            if not (body == "-" or body.startswith("- ")):
                break

            pos += 1
            item_text = body[1:].strip() if body != "-" else ""

            if item_text:
                parts = _split_top(item_text, ":")

                if len(parts) >= 2:
                    item, pos = _parse_compact_mapping(
                        tokens,
                        pos,
                        indent,
                        item_text,
                        depth + 1,
                    )
                    out.append(item)
                else:
                    out.append(_parse_scalar(item_text, depth + 1))
            else:
                if pos < len(tokens) and tokens[pos][0] > indent:
                    value, pos = _parse_block(
                        tokens,
                        pos,
                        tokens[pos][0],
                        depth + 1,
                    )
                    out.append(value)
                else:
                    out.append(None)

        return out, pos

    out = {}

    while pos < len(tokens):
        ind, body = tokens[pos]

        if ind < indent:
            break

        if ind > indent:
            raise ValueError("Unexpected indentation in YAML mapping")

        if body == "-" or body.startswith("- "):
            break

        parts = _split_top(body, ":")
        if len(parts) < 2:
            raise ValueError("Expected YAML key: value")

        key = _parse_scalar(parts[0], depth + 1)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be strings")
        if key in out:
            raise ValueError("Duplicate YAML configuration key: " + key)

        value_text = ":".join(parts[1:]).strip()
        pos += 1

        if value_text:
            out[key] = _parse_scalar(value_text, depth + 1)
        else:
            if pos < len(tokens) and tokens[pos][0] > indent:
                value, pos = _parse_block(
                    tokens,
                    pos,
                    tokens[pos][0],
                    depth + 1,
                )
                out[key] = value
            elif (
                pos < len(tokens)
                and tokens[pos][0] == indent
                and (
                    tokens[pos][1] == "-"
                    or tokens[pos][1].startswith("- ")
                )
            ):
                # Indentless sequence:
                # key:
                # - item
                value, pos = _parse_block(tokens, pos, indent, depth + 1)
                out[key] = value
            else:
                out[key] = None

    return out, pos


def _validate_data(value, depth=0, ancestors=None):
    """Bound recursion and reject lossy types, cycles and non-finite JSON too."""
    if depth > _MAX_DEPTH:
        raise ValueError("Configuration nesting too deep")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite number is not supported")
        return value
    if not isinstance(value, (dict, list)):
        raise ValueError("Unsupported configuration type: " + type(value).__name__)
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError("Cyclic configuration is not supported")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Configuration keys must be strings")
            values = value.values()
        else:
            values = value
        for item in values:
            _validate_data(item, depth + 1, ancestors)
    finally:
        ancestors.remove(id(value))
    return value


def safe_load(source):
    """
    Load configuration from a string, bytes, or file-like object.

    This is intended as a drop-in replacement for yaml.safe_load() within the
    bounded FileNazm configuration dialect.
    """
    if hasattr(source, "read"):
        text = source.read()
    else:
        text = source

    if isinstance(text, bytes):
        text = text.decode("utf-8")

    if len(text) > _MAX_TEXT_CHARS:
        raise ValueError("Configuration exceeds 4 MiB limit")

    text = text.lstrip("\ufeff")

    # Fast path: valid JSON.
    # Duplicate JSON keys are rejected explicitly.
    def _object_pairs_hook(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate configuration key: " + key)
            out[key] = value
        return out

    try:
        return _validate_data(json.loads(text, object_pairs_hook=_object_pairs_hook))
    except json.JSONDecodeError:
        pass

    tokens = _tokenize(text)

    if not tokens:
        return None

    # Allow a single top-level flow collection.
    if len(tokens) == 1:
        body = tokens[0][1]
        if body != "-" and not body.startswith("- ") and len(_split_top(body, ":")) == 1:
            return _validate_data(_parse_scalar(body, 0))

    result, pos = _parse_block(tokens, 0, tokens[0][0], 0)

    if pos != len(tokens):
        raise ValueError("Unsupported or inconsistent YAML indentation")

    return _validate_data(result)


def _sort_recursive(value):
    if isinstance(value, dict):
        return {key: _sort_recursive(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_sort_recursive(item) for item in value]
    return value


def _format_string(value, allow_unicode):
    """
    Return a YAML scalar representation for a string.

    The strategy is conservative: if a string could be interpreted as another
    type or contains structural characters, it is emitted as a double-quoted
    JSON-compatible YAML string.
    """
    if value == "":
        return '""'

    if value in ("...", "---") or any(ord(ch) < 32 for ch in value):
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if re.match(r"(?:\d{4}-\d{2}-\d{2}|[-+]?0[xXoObB][0-9a-fA-F]+)", value):
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if value != value.strip():
        return json.dumps(value, ensure_ascii=not allow_unicode)

    low = value.lower()

    if low in _RESERVED_WORDS:
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if _INT_RE.match(value) or _FLOAT_RE.match(value):
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if any(ch in _SPECIAL_CHARS for ch in value):
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if value[0] in _LEADING_SPECIAL:
        return json.dumps(value, ensure_ascii=not allow_unicode)

    if not allow_unicode and any(ord(ch) > 127 for ch in value):
        return json.dumps(value, ensure_ascii=True)

    return value


def _format_scalar(value, allow_unicode):
    if value is None:
        return "null"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite float cannot be serialized")
        return json.dumps(value)

    if isinstance(value, str):
        return _format_string(value, allow_unicode)

    if isinstance(value, dict):
        if not value:
            return "{}"
        # Non-empty dictionaries should be handled by _dump_lines.
        # This fallback is only for unexpected scalar-context usage.
        return json.dumps(value, ensure_ascii=not allow_unicode, sort_keys=True)

    if isinstance(value, list):
        if not value:
            return "[]"
        return json.dumps(value, ensure_ascii=not allow_unicode)

    return json.dumps(str(value), ensure_ascii=not allow_unicode)


def _dump_lines(value, indent, allow_unicode):
    """
    Convert a Python object into YAML block lines.

    Returns a list of strings without trailing newline characters.
    """
    pad = "  " * indent
    lines = []

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = _format_string(str(key), allow_unicode)

            if isinstance(item, dict) and item:
                lines.append(pad + key_text + ":")
                lines.extend(_dump_lines(item, indent + 1, allow_unicode))
            elif isinstance(item, list) and item:
                lines.append(pad + key_text + ":")
                lines.extend(_dump_lines(item, indent + 1, allow_unicode))
            else:
                lines.append(
                    pad + key_text + ": " + _format_scalar(item, allow_unicode)
                )

        return lines

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                child = _dump_lines(item, indent + 1, allow_unicode)
                if child:
                    child_prefix = "  " * (indent + 1)
                    if child[0].startswith(child_prefix):
                        child[0] = pad + "- " + child[0][len(child_prefix):]
                    else:
                        child[0] = pad + "- " + child[0]
                    lines.extend(child)
                else:
                    lines.append(pad + "- {}")

            elif isinstance(item, list) and item:
                lines.append(pad + "-")
                lines.extend(_dump_lines(item, indent + 1, allow_unicode))

            else:
                lines.append(pad + "- " + _format_scalar(item, allow_unicode))

        return lines

    lines.append(pad + _format_scalar(value, allow_unicode))
    return lines


def safe_dump(
    data,
    stream=None,
    allow_unicode=True,
    sort_keys=False,
    default_flow_style=False,
    **kwargs
):
    """
    Serialize data to human-readable block YAML.

    This signature is compatible with common PyYAML calls:
        yaml.safe_dump(data)
        yaml.safe_dump(data, stream)
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        yaml.safe_dump(data, stream, allow_unicode=True, default_flow_style=False)

    Unknown keyword arguments are ignored for compatibility.
    """
    # default_flow_style and extra PyYAML kwargs are accepted but not used.
    # This module always emits a bounded block-YAML subset.
    _validate_data(data)
    if sort_keys:
        data = _sort_recursive(data)

    if isinstance(data, dict) and not data:
        text = "{}\n"
    elif isinstance(data, list) and not data:
        text = "[]\n"
    else:
        lines = _dump_lines(data, 0, allow_unicode)
        if lines:
            text = "\n".join(lines) + "\n"
        else:
            text = "null\n"

    if len(text) > _MAX_TEXT_CHARS:
        raise ValueError("Configuration exceeds 4 MiB limit")
    if stream is not None:
        stream.write(text)
        return None

    return text


# Optional aliases for convenience.
load = safe_load
dump = safe_dump
