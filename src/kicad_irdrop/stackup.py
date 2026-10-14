"""Read the physical stack-up straight from a .kicad_pcb file.

KiCad's Python bindings do not expose BOARD_STACKUP, so this parses the ``(stackup ...)`` block of the board file
itself. Only the copper layers' thickness and their position in the stack are needed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_OUTER_CU = 0.035   # mm, 1 oz
DEFAULT_INNER_CU = 0.0175  # mm, 0.5 oz
DEFAULT_BOARD = 1.6        # mm


def _sexpr(text: str, start: int) -> tuple[list, int]:
    """Parse one s-expression starting at text[start] == '('. Returns (nested list, index after it)."""
    out: list = []
    stack = [out]
    i = start
    tok = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')
    while True:
        m = tok.match(text, i)
        if not m:
            raise ValueError("unterminated s-expression")
        t = m.group(1)
        i = m.end()
        if t == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            stack.pop()
            if len(stack) == 1:
                return out[0], i
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)


@dataclass
class Layer:
    name: str
    kind: str                    # "copper" or the dielectric type ("core", "prepreg", ...)
    thickness: float             # mm
    epsilon_r: float | None = None


@dataclass
class Stackup:
    layers: list[Layer] = field(default_factory=list)   # top to bottom, copper and dielectric only

    @property
    def copper(self) -> list[Layer]:
        return [l for l in self.layers if l.kind == "copper"]

    def copper_thickness(self, name: str) -> float:
        for l in self.copper:
            if l.name == name:
                return l.thickness
        return DEFAULT_OUTER_CU if name in ("F.Cu", "B.Cu") else DEFAULT_INNER_CU

    def z_mid(self) -> dict[str, float]:
        """Distance (mm) from the top of the top copper to the middle of each copper layer."""
        z, out = 0.0, {}
        for l in self.layers:
            if l.kind == "copper":
                out[l.name] = z + l.thickness / 2
            z += l.thickness
        return out


def read_stackup(path: str, copper_names: list[str] | None = None) -> Stackup:
    """Stack-up of the board file at ``path``. If the file has no stack-up block, a uniform 1.6 mm stack is made up
    for ``copper_names`` (top to bottom) with 1 oz outer and 0.5 oz inner copper, and flagged as assumed."""
    text = open(path, encoding="utf-8").read()
    i = text.find("(stackup")
    st = Stackup()
    if i >= 0:
        node, _ = _sexpr(text, i)
        for item in node[1:]:
            if not (isinstance(item, list) and item and item[0] == "layer"):
                continue
            name = item[1]
            props: dict[str, list[str]] = {}
            for p in item[2:]:
                if isinstance(p, list) and p:
                    props.setdefault(p[0], []).append(p[1] if len(p) > 1 else "")
            kind = (props.get("type") or [""])[0]
            if kind == "copper" or name.endswith(".Cu"):
                st.layers.append(Layer(name, "copper", float((props.get("thickness") or [DEFAULT_INNER_CU])[0])))
            elif "thickness" in props and not any(s in kind.lower() for s in ("mask", "silk", "paste")):
                # a dielectric may hold several sub-layers: sum their thickness, keep the first permittivity
                th = sum(float(v) for v in props["thickness"])
                er = float(props["epsilon_r"][0]) if "epsilon_r" in props else None
                st.layers.append(Layer(name, kind or "dielectric", th, er))
    if st.copper:
        return st
    names = copper_names or ["F.Cu", "B.Cu"]
    gap = (DEFAULT_BOARD - 2 * DEFAULT_OUTER_CU - (len(names) - 2) * DEFAULT_INNER_CU) / max(1, len(names) - 1)
    for k, n in enumerate(names):
        st.layers.append(Layer(n, "copper", DEFAULT_OUTER_CU if k in (0, len(names) - 1) else DEFAULT_INNER_CU))
        if k < len(names) - 1:
            st.layers.append(Layer(f"assumed dielectric {k + 1}", "assumed", gap, 4.3))
    return st
