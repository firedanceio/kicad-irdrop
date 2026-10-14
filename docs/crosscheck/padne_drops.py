"""Read padne's ParaView export of the cross-check board and print the drop at each load pad.

Usage: python3 padne_drops.py vtu/F.Cu.vtu
The pad positions match make_xcheck_board.py: source J1 at (4, 15), load U1 on a 2 mm 3x3 grid around (30, 15).
"""
import math
import re
import statistics as st
import sys

pts = []
text = open(sys.argv[1]).read()
for piece in re.findall(r"<Piece.*?</Piece>", text, re.S):
    v = [float(x) for x in re.search(r'Name="voltage">([^<]*)<', piece).group(1).split()]
    xyz = [float(x) for x in re.search(r'NumberOfComponents="3">([^<]*)<', piece).group(1).split()]
    pts += [(xyz[3 * i], -xyz[3 * i + 1], vv) for i, vv in enumerate(v) if not math.isnan(vv)]   # padne's y is up


def mean_v(cx, cy, r):
    return st.mean(v for x, y, v in pts if (x - cx) ** 2 + (y - cy) ** 2 <= r * r)


v_src = mean_v(4, 15, 1.0)
grid = [(30 + dx, 15 + dy) for dy in (-2, 0, 2) for dx in (-2, 0, 2)]
drops = [(v_src - mean_v(x, y, 0.25)) * 1000 for x, y in grid]
print("drop per load pad, mV:", " ".join(f"{d:.2f}" for d in drops))
print(f"worst {max(drops):.2f} mV, median {st.median(drops):.2f} mV, mean drop / current {st.mean(drops) / 2:.2f} mOhm (2 A)")
