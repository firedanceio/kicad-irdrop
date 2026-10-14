"""Rewrite a KiCad 10 board into KiCad 8 syntax on a KiCad 8 template header (for padne's bundled KiCad)."""
import re, sys
sys.path.insert(0, sys.path[0])
from sexp_blocks import blocks
tpl, src, out = sys.argv[1:4]
head = [b for b in blocks(open(tpl).read()) if b.split()[0].strip("(") in ("version", "generator", "generator_version", "general", "paper", "layers", "setup")]
body = [b for b in blocks(open(src).read()) if b.split()[0].strip("(") in ("footprint", "gr_line", "segment", "via", "zone")]
names = sorted({m for b in body for m in re.findall(r'\(net "([^"]*)"\)', b)} - {""})
ids = {"": 0, **{n: i + 1 for i, n in enumerate(names)}}
res = []
for b in head:
    res.append(b)
    if b.startswith("(setup"):
        res += [f'(net {i} "{n}")' for n, i in ids.items()]
for b in body:
    kind = b.split()[0].strip("(")
    b = re.sub(r'\n\s*\(embedded_fonts [a-z]+\)', "", b)
    for t in ("tenting", "capping", "covering", "plugging", "filling", "duplicate_pad_numbers_are_jumpers"):
        b = re.sub(r'\n\s*\(' + t + r'\b(?:[^()]|\([^()]*\))*\)', "", b)
    if kind == "zone":
        b = re.sub(r'\(net "([^"]*)"\)', lambda m: f'(net {ids[m.group(1)]})\n\t\t(net_name "{m.group(1)}")', b)
    elif kind in ("segment", "via"):
        b = re.sub(r'\(net "([^"]*)"\)', lambda m: f'(net {ids[m.group(1)]})', b)
    else:
        b = re.sub(r'\(net "([^"]*)"\)', lambda m: f'(net {ids[m.group(1)]} "{m.group(1)}")', b)
    res.append(b)
open(out, "w").write("(kicad_pcb\n" + "\n".join(res) + "\n)\n")
