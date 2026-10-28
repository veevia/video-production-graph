"""A small YAML subset parser, used only when PyYAML is not importable.

It understands block mappings and sequences, flow collections, plain and quoted scalars
(including quoted scalars that span lines), comments and literal or folded block scalars.
That covers vpg.project.yaml and the engine's graph/workflow.yaml. Anchors, aliases, tags
and multi-document streams are not supported; plain scalars follow YAML 1.2 core typing.
"""
from __future__ import annotations

import re


class YamlSubsetError(ValueError):
    pass


class _Line:
    __slots__ = ("indent", "text", "block")

    def __init__(self, indent, text, block=None):
        self.indent = indent
        self.text = text
        self.block = block


_INT = re.compile(r"^[-+]?(0|[1-9][0-9_]*)$")
_FLOAT = re.compile(r"^[-+]?(\.[0-9]+|[0-9][0-9_]*(\.[0-9_]*)?)([eE][-+]?[0-9]+)?$")
_BLOCK_MARK = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)$")
_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ",
    "e": "\x1b", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "N": "\x85", "_": "\xa0",
    "L": "\u2028", "P": "\u2029",
}


def loads(text):
    lines = _logical_lines(text)
    if not lines:
        return None
    value, index = _parse_node(lines, 0)
    if index < len(lines):
        raise YamlSubsetError(f"unexpected content: {lines[index].text[:40]!r}")
    return value


def _is_seq_item(text):
    return text == "-" or text.startswith("- ")


def _quoted_end(text, pos):
    quote = text[pos]
    i = pos + 1
    while i < len(text):
        char = text[i]
        if quote == '"' and char == "\\":
            i += 2
            continue
        if char == quote:
            if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return None


def _split_key(text):
    """Return (key, rest) when the line is a mapping entry, else None."""
    if not text or _is_seq_item(text) or text[0] in "[{":
        return None
    if text[0] in "'\"":
        end = _quoted_end(text, 0)
        if end is None:
            return None
        after = text[end:]
        if after.startswith(":") and (len(after) == 1 or after[1] == " "):
            return _unquote(text[:end]), after[1:].strip()
        return None
    index = text.find(": ")
    if index == -1:
        if text.endswith(":"):
            index = len(text) - 1
        else:
            return None
    key = text[:index].strip()
    if not key or key.startswith("#"):
        return None
    return key, text[index + 1:].strip()


def _value_start(content):
    """Index where the value of a line starts (after '- ' or 'key: ')."""
    offset = 0
    while _is_seq_item(content[offset:]):
        offset += 1
        while offset < len(content) and content[offset] == " ":
            offset += 1
    rest = content[offset:]
    split = _split_key(rest)
    if split is not None:
        key_text, value = split
        if not value:
            return len(content)
        return len(content) - len(value)
    return offset


def _scan_balanced(text, start):
    """Scan a quoted or flow value from start; return (balanced, comment_index)."""
    depth = 0
    i = start
    in_quote = None
    while i < len(text):
        char = text[i]
        if in_quote:
            if in_quote == '"' and char == "\\":
                i += 2
                continue
            if char == in_quote:
                if in_quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_quote = None
                if depth == 0:
                    return True, _comment_index(text, i + 1)
        elif char in "'\"" and (i == start or text[i - 1] in " ,[{:"):
            in_quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return True, _comment_index(text, i + 1)
        elif char == "#" and depth > 0 and text[i - 1] in " \t":
            # a comment inside an open flow collection: drop the rest of this physical line
            return False, None
        i += 1
    return False, None


def _comment_index(text, start):
    i = text.find("#", start)
    while i != -1:
        if i == 0 or text[i - 1] in " \t":
            return i
        i = text.find("#", i + 1)
    return None


def _strip_plain_comment(text):
    index = _comment_index(text, 0)
    if index is None:
        return text.rstrip()
    return text[:index].rstrip()


def _logical_lines(text):
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    while i < len(raw):
        line = raw[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if line[:indent + 1].endswith("\t"):
            raise YamlSubsetError("tabs are not allowed in indentation")
        content = line[indent:].rstrip()
        start = _value_start(content)
        if start < len(content) and content[start] in "'\"[{":
            while True:
                balanced, comment = _scan_balanced(content, start)
                if balanced:
                    if comment is not None:
                        content = content[:comment].rstrip()
                    break
                i += 1
                if i >= len(raw):
                    raise YamlSubsetError("unterminated quoted or flow value")
                nxt = raw[i].strip()
                if nxt.startswith("#"):
                    continue
                content = f"{content} {nxt}" if nxt else content + "\n"
        else:
            content = _strip_plain_comment(content)
        block = None
        marker = _BLOCK_MARK.match(content[start:]) if start < len(content) else None
        if marker:
            content = content[:start].rstrip()
            block, i = _read_block_scalar(raw, i, indent, marker)
            out.append(_Line(indent, content, block))
            continue
        out.append(_Line(indent, content))
        i += 1
    return out


def _read_block_scalar(raw, i, parent_indent, marker):
    style = marker.group(1)
    chomp = marker.group(2) or marker.group(4)
    explicit = int(marker.group(3)) if marker.group(3) else None
    body = []
    j = i + 1
    block_indent = None
    while j < len(raw):
        line = raw[j]
        if line.strip() == "":
            body.append("")
            j += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = parent_indent + explicit if explicit else indent
        body.append(line[block_indent:] if indent >= block_indent else line.strip())
        j += 1
    while body and body[-1] == "":
        body.pop()
    if style == "|":
        value = "\n".join(body)
    else:
        parts = []
        for piece in body:
            if piece == "":
                parts.append("\n")
            elif parts and not parts[-1].endswith("\n"):
                parts.append(" " + piece)
            else:
                parts.append(piece)
        value = "".join(parts)
    if chomp != "-" and value:
        value += "\n"
    return value, j


def _parse_node(lines, index):
    line = lines[index]
    if _is_seq_item(line.text):
        return _parse_seq(lines, index, line.indent)
    if _split_key(line.text) is not None:
        return _parse_map(lines, index, line.indent)
    return _scalar_or_flow(line.text, line.block), index + 1


def _parse_seq(lines, index, indent):
    items = []
    while index < len(lines) and lines[index].indent == indent and _is_seq_item(lines[index].text):
        line = lines[index]
        rest = line.text[1:].lstrip(" ")
        offset = len(line.text) - len(rest)
        if not rest:
            if line.block is not None:
                value, index = line.block, index + 1
            elif index + 1 < len(lines) and lines[index + 1].indent > indent:
                value, index = _parse_node(lines, index + 1)
            else:
                value, index = None, index + 1
        elif _is_seq_item(rest) or (_split_key(rest) is not None and rest[0] not in "'\"[{"):
            lines[index] = _Line(indent + offset, rest, line.block)
            value, index = _parse_node(lines, index)
        else:
            value, index = _scalar_or_flow(rest, line.block), index + 1
        items.append(value)
    return items, index


def _parse_map(lines, index, indent):
    result = {}
    while index < len(lines) and lines[index].indent == indent and not _is_seq_item(lines[index].text):
        line = lines[index]
        split = _split_key(line.text)
        if split is None:
            raise YamlSubsetError(f"expected a mapping key: {line.text[:40]!r}")
        key, rest = split
        index += 1
        if line.block is not None:
            value = line.block
        elif rest == "":
            if index < len(lines) and lines[index].indent > indent:
                value, index = _parse_node(lines, index)
            elif index < len(lines) and lines[index].indent == indent and _is_seq_item(lines[index].text):
                value, index = _parse_seq(lines, index, indent)
            else:
                value = None
        else:
            value = _scalar_or_flow(rest, None)
            if isinstance(value, str) and rest[0] not in "'\"[{":
                while (index < len(lines) and lines[index].indent > indent
                       and not _is_seq_item(lines[index].text) and _split_key(lines[index].text) is None):
                    value = f"{value} {lines[index].text}"
                    index += 1
        result[key] = value
    return result, index


def _scalar_or_flow(text, block):
    if block is not None:
        return block
    text = text.strip()
    if not text:
        return None
    if text[0] in "[{":
        value, _ = _flow(text, 0)
        return value
    if text[0] in "'\"":
        end = _quoted_end(text, 0)
        if end is None:
            raise YamlSubsetError("unterminated quoted scalar")
        return _unquote(text[:end])
    return _plain(text)


def _skip_ws(text, pos):
    while pos < len(text) and text[pos] in " \n\t":
        pos += 1
    return pos


def _flow(text, pos):
    pos = _skip_ws(text, pos)
    if pos >= len(text):
        raise YamlSubsetError("unexpected end of flow value")
    char = text[pos]
    if char == "[":
        items = []
        pos += 1
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise YamlSubsetError("unterminated flow sequence")
            if text[pos] == "]":
                return items, pos + 1
            value, pos = _flow(text, pos)
            items.append(value)
            pos = _skip_ws(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
            elif pos < len(text) and text[pos] == "]":
                return items, pos + 1
            else:
                raise YamlSubsetError("expected ',' or ']' in flow sequence")
    if char == "{":
        result = {}
        pos += 1
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise YamlSubsetError("unterminated flow mapping")
            if text[pos] == "}":
                return result, pos + 1
            key, pos = _flow_scalar(text, pos, as_key=True)
            pos = _skip_ws(text, pos)
            value = None
            if pos < len(text) and text[pos] == ":":
                pos = _skip_ws(text, pos + 1)
                if pos < len(text) and text[pos] not in ",}":
                    value, pos = _flow(text, pos)
            result[key] = value
            pos = _skip_ws(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
            elif pos < len(text) and text[pos] == "}":
                return result, pos + 1
            else:
                raise YamlSubsetError("expected ',' or '}' in flow mapping")
    return _flow_scalar(text, pos)


def _flow_scalar(text, pos, as_key=False):
    if text[pos] in "'\"":
        end = _quoted_end(text, pos)
        if end is None:
            raise YamlSubsetError("unterminated quoted scalar")
        return _unquote(text[pos:end]), end
    start = pos
    while pos < len(text):
        char = text[pos]
        if char in ",]}":
            break
        if char == ":" and (pos + 1 >= len(text) or text[pos + 1] in " ,]}\n"):
            break
        pos += 1
    raw = text[start:pos].strip()
    return (raw if as_key else _plain(raw)), pos


def _unquote(token):
    quote = token[0]
    body = token[1:-1]
    if quote == "'":
        return body.replace("''", "'")
    out = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            code = body[i + 1]
            width = {"x": 2, "u": 4, "U": 8}.get(code)
            if width:
                out.append(chr(int(body[i + 2:i + 2 + width], 16)))
                i += 2 + width
                continue
            out.append(_ESCAPES.get(code, code))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _plain(raw):
    if raw in ("", "~", "null", "Null", "NULL"):
        return None
    if raw in ("true", "True", "TRUE"):
        return True
    if raw in ("false", "False", "FALSE"):
        return False
    if _INT.match(raw):
        return int(raw.replace("_", ""))
    if _FLOAT.match(raw) and any(ch.isdigit() for ch in raw):
        return float(raw.replace("_", ""))
    return raw
