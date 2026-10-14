"""Top-level blocks of a KiCad s-expression file, as strings (used by to_kicad8.py)."""


def blocks(text: str) -> list[str]:
    """The direct children of the root list, e.g. "(version ...)", "(footprint ...)", in file order."""
    out, i = [], text.index("(", 1)
    while i < len(text):
        c = text[i]
        if c == ")":
            break
        if c != "(":
            i += 1
            continue
        depth, j = 0, i
        while True:
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == '"':
                j += 1
                while text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
            j += 1
        out.append(text[i:j + 1])
        i = j + 1
    return out
