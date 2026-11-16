import re
import unicodedata


def clean(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text))
    return "".join(c for c in text if not unicodedata.category(c).startswith("C"))


def cell_width(char):
    return 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1


def text_width(text):
    return sum(cell_width(c) for c in text)


def slice_cells(text, start, width):
    """Clip terminal cells without cutting wide characters in half."""
    result, position = "", 0
    for char in clean(text):
        size = cell_width(char)
        if size == 0:
            if result:
                result += char
            continue
        end = position + size
        if position >= start + width:
            break
        if end > start:
            if position < start or end > start + width:
                result += " " * max(0, min(end, start + width) - max(position, start))
            else:
                result += char
        position = end
    return result


def fit(text, width):
    return slice_cells(text, 0, max(0, width))
