"""Draw padne's solution of one layer in kicad-irdrop's map colours, for a side-by-side comparison.

Usage: python3 padne_map.py vtu/In2.Cu.vtu out.png X0 X1 Y0 Y1 [PX_PER_MM]
Current density per mesh triangle is sigma * |grad V| (sigma 5.8e4 S/mm, as in the cross-check), on the same log
scale kicad-irdrop uses: log10(J) / 2.5, blue at 1 A/mm2 or below, red at 316 A/mm2; grey is copper under 1 A/mm2.
Needs numpy and matplotlib.
"""
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection

SIGMA = 5.8e4
src, out = sys.argv[1], sys.argv[2]
x0, x1, y0, y1 = (float(v) for v in sys.argv[3:7])
ppm = float(sys.argv[7]) if len(sys.argv) > 7 else 40.0

polys, colours = [], []
text = open(src).read()
for piece in re.findall(r"<Piece.*?</Piece>", text, re.S):
    arr = lambda name: re.search(r'Name="' + name + r'"[^>]*>([^<]*)<', piece).group(1).split()
    V = np.array([float(v) for v in arr("voltage")])
    P = np.array([float(v) for v in re.search(r'NumberOfComponents="3">([^<]*)<', piece).group(1).split()]).reshape(-1, 3)
    P[:, 1] = -P[:, 1]                                  # padne's y is up, KiCad's is down
    conn = np.array([int(v) for v in arr("connectivity")])
    offs = np.array([int(v) for v in arr("offsets")])
    start = 0
    for end in offs:
        tri = conn[start:end]
        start = end
        if len(tri) != 3 or np.isnan(V[tri]).any():
            continue
        (xa, ya), (xb, yb), (xc, yc) = P[tri, :2]
        A = np.array([[xb - xa, yb - ya], [xc - xa, yc - ya]])
        dv = np.array([V[tri[1]] - V[tri[0]], V[tri[2]] - V[tri[0]]])
        try:
            g = np.linalg.solve(A, dv)
        except np.linalg.LinAlgError:
            continue
        J = SIGMA * np.hypot(*g)
        lv = np.clip(np.log10(J + 1e-9) / 2.5, 0, 1)
        colours.append((60 / 255,) * 3 if lv <= 0 else (lv, 1 - abs(lv - 0.5) * 2, 1 - lv))
        polys.append(P[tri, :2])

fig = plt.figure(figsize=((x1 - x0) * ppm / 100, (y1 - y0) * ppm / 100), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_facecolor("black")
ax.add_collection(PolyCollection(polys, facecolors=colours, edgecolors="face", linewidths=0.2))
ax.set_xlim(x0, x1)
ax.set_ylim(y1, y0)
ax.set_aspect("equal")
ax.axis("off")
fig.savefig(out, facecolor="black")
print(f"{len(polys)} triangles -> {out}")
