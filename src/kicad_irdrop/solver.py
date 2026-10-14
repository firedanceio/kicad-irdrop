"""DC IR-drop solve of one power net on a KiCad board.

Model
-----
* Every copper layer that carries the net is rasterised on a square grid (``grid`` mm): filled zones, tracks (arcs
  approximated through their midpoint) and pads. Neighbouring copper cells are joined by the sheet conductance
  sigma * t, with t the layer's copper thickness from the board's stack-up.
* A via is a chain of barrel segments between the copper layers it spans. A plated through/blind via is a tube of
  wall ``plating`` mm (R = rho * dz / (pi * (d + plating) * plating)); a laser microvia, or any via with
  ``filled_vias``, is a solid copper cylinder of its drill diameter. On each layer the barrel node is tied to every
  copper cell under the via's ring, so a via is never modelled as a point contact.
* The source part's pad(s) on the net are held at 0 V. Each load pad (all pads of the load parts on the net) sinks an
  equal share of the current. The drop reported at a pad is the mean over its cells.
* If a sense part is given (the regulator's feedback point, e.g. the top resistor of its divider), the drop from that
  part's pads to the worst load is also reported: that is the drop the load actually sees once the regulator closes
  its loop.

Limits: DC only (no inductance, no decoupling), uniform load sharing, copper conductivity at 20 C, no solder or
package resistance. It is a layout comparison tool and a sanity check, not a sign-off PDN simulator.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .stackup import Stackup

SIGMA = 5.8e4          # S/mm, annealed copper at 20 C
RHO = 1.0 / SIGMA      # ohm*mm


@dataclass
class Rail:
    net: str
    source: str                     # reference of the part whose pads feed the net (inductor, regulator)
    current: float                  # A
    loads: list[str] = field(default_factory=list)   # load part references
    sense: str | None = None        # reference of the feedback/sense part, optional
    vnom: float | None = None       # nominal rail voltage, for the budget

    @classmethod
    def parse(cls, spec: str) -> "Rail":
        """NET:SOURCE:AMPS[:LOAD1+LOAD2[:SENSE[:VNOM]]]"""
        p = spec.split(":")
        if len(p) < 3:
            raise ValueError(f"rail spec {spec!r}: need NET:SOURCE:AMPS[:LOADS[:SENSE[:VNOM]]]")
        loads = [x for x in p[3].split("+") if x] if len(p) > 3 and p[3] else []
        return cls(p[0], p[1], float(p[2]), loads, (p[4] or None) if len(p) > 4 else None,
                   float(p[5]) if len(p) > 5 and p[5] else None)


@dataclass
class Barrel:
    amps: float
    x: float
    y: float
    layer_a: str
    layer_b: str
    rating: float


@dataclass
class Result:
    rail: Rail
    loads: list[str]
    worst_mv: float
    worst_pad: str
    median_mv: float
    unreached: list[str]
    sense_mv: float | None          # drop from the source to the sense point
    vias: int
    barrels: list[Barrel]           # busiest first
    peak_density: float             # A/mm2
    peak_where: tuple[str, float, float]
    loss_share: list[tuple[str, float, float, float]]   # (layer or "via", x, y, fraction of the total loss)
    budget_mv: float | None

    @property
    def r_eff_mohm(self) -> float:
        return self.worst_mv / self.rail.current

    @property
    def seen_mv(self) -> float:
        """Drop the load sees: from the sense point if there is one, else from the source."""
        return self.worst_mv - (self.sense_mv or 0.0)

    @property
    def over_budget(self) -> bool | None:
        return None if self.budget_mv is None else self.seen_mv > self.budget_mv

    def as_dict(self) -> dict:
        return {"net": self.rail.net, "source": self.rail.source, "current_a": self.rail.current, "loads": self.loads,
                "worst_drop_mv": round(self.worst_mv, 3), "worst_pad": self.worst_pad, "median_drop_mv": round(self.median_mv, 3),
                "r_eff_mohm": round(self.r_eff_mohm, 3), "sense": self.rail.sense,
                "sense_drop_mv": None if self.sense_mv is None else round(self.sense_mv, 3),
                "seen_drop_mv": round(self.seen_mv, 3), "budget_mv": self.budget_mv, "over_budget": self.over_budget,
                "unreached": self.unreached, "vias": self.vias,
                "barrels_over_rating": sum(1 for b in self.barrels if b.amps > b.rating),
                "busiest_barrels": [b.__dict__ for b in self.barrels[:10]],
                "peak_density_a_per_mm2": round(self.peak_density, 1), "peak_where": self.peak_where,
                "loss_share": [{"where": w, "x": x, "y": y, "fraction": round(f, 4)} for w, x, y, f in self.loss_share]}


def ipc2221_internal_amps(area_mm2: float, dt: float = 10.0) -> float:
    """IPC-2221 current for an internal conductor of the given cross-section (mm2) at a dt (C) rise."""
    return 0.024 * dt ** 0.44 * (area_mm2 / 0.0254 ** 2) ** 0.725


def _poly_set(ps, i, M):
    o = ps.Outline(i)
    pts = [(M(o.CPoint(k).x), M(o.CPoint(k).y)) for k in range(o.PointCount())]
    holes = [[(M(ps.Hole(i, h).CPoint(k).x), M(ps.Hole(i, h).CPoint(k).y)) for k in range(ps.Hole(i, h).PointCount())]
             for h in range(ps.HoleCount(i))]
    return Polygon(pts, holes).buffer(0) if len(pts) >= 3 else None


def solve(board, stack: Stackup, rail: Rail, grid: float = 0.05, plating: float = 0.020, filled_vias: bool = False,
          budget_pct: float | None = 2.0, map_dir: str | None = None) -> Result:
    import pcbnew
    M = pcbnew.ToMM
    b = board
    # stack-up lookups use KiCad's standard layer names (the stack-up block is keyed by them); output uses the board's
    # own names, which a designer may have changed (e.g. "In1.GND")
    lids = list(b.GetEnabledLayers().CuStack())
    cu = [pcbnew.BOARD.GetStandardLayerName(l) for l in lids]
    show = {pcbnew.BOARD.GetStandardLayerName(l): b.GetLayerName(l) for l in lids}
    std = lambda lid: pcbnew.BOARD.GetStandardLayerName(lid)
    th = {L: stack.copper_thickness(L) for L in cu}
    zmid = stack.z_mid()
    zl = {L: zmid.get(L, k * 1.6 / max(1, len(cu) - 1)) for k, L in enumerate(cu)}
    nc = b.GetNetcodeFromNetname(rail.net)
    if nc <= 0:
        raise ValueError(f"net {rail.net!r} is not on the board")
    loads = rail.loads or ["U1"]

    shapes: dict[str, list] = {}
    for zn in b.Zones():
        if zn.GetNetCode() != nc or zn.GetIsRuleArea():
            continue
        for lid in zn.GetLayerSet().CuStack():
            fp_ = zn.GetFilledPolysList(lid)
            for i in range(fp_.OutlineCount()):
                p = _poly_set(fp_, i, M)
                if p is not None:
                    shapes.setdefault(std(lid), []).append(p)
    vias = []
    for t in b.GetTracks():
        if t.GetNetCode() != nc:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append(t)
            continue
        pts = [(M(t.GetStart().x), M(t.GetStart().y))]
        if t.Type() == pcbnew.PCB_ARC_T:
            pts.append((M(t.GetMid().x), M(t.GetMid().y)))
        pts.append((M(t.GetEnd().x), M(t.GetEnd().y)))
        shapes.setdefault(std(t.GetLayer()), []).append(LineString(pts).buffer(M(t.GetWidth()) / 2))
    src_pads, load_pads, sense_pads = [], [], []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            if p.GetNetCode() != nc:
                continue
            for lid, L in zip(lids, cu):
                if not p.IsOnLayer(lid):
                    continue
                ep = p.GetEffectivePolygon(lid)
                pp = unary_union([g for g in (_poly_set(ep, i, M) for i in range(ep.OutlineCount())) if g is not None])
                if pp.is_empty:
                    continue
                shapes.setdefault(L, []).append(pp)
                if ref == rail.source:
                    src_pads.append((L, pp))
                if ref in loads:
                    load_pads.append((f"{ref}.{p.GetNumber()}", L, pp))
                if rail.sense and ref == rail.sense:
                    sense_pads.append((L, pp))
    if not src_pads:
        raise ValueError(f"{rail.net}: source {rail.source} has no pad on the net")
    if not load_pads:
        raise ValueError(f"{rail.net}: none of the loads {loads} has a pad on the net")

    allg = unary_union([unary_union(v) for v in shapes.values()])
    x0, y0, x1, y1 = allg.bounds
    ox, oy = x0 - grid, y0 - grid
    nx, ny = int((x1 - x0) / grid) + 3, int((y1 - y0) / grid) + 3
    xs = ox + (np.arange(nx) + 0.5) * grid
    ys = oy + (np.arange(ny) + 0.5) * grid
    XX, YY = np.meshgrid(xs, ys)

    def cell(x, y):
        return int((y - oy) // grid), int((x - ox) // grid)

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    nn = 0
    node: dict[str, np.ndarray] = {}
    for L, shp in shapes.items():
        m = shapely.contains_xy(unary_union(shp), XX, YY)
        idx = -np.ones(m.shape, dtype=np.int64)
        cnt = int(m.sum())
        idx[m] = np.arange(nn, nn + cnt)
        nn += cnt
        node[L] = idx
        g = SIGMA * th[L]
        for a, c in ((idx[:, :-1], idx[:, 1:]), (idx[:-1, :], idx[1:, :])):
            ok = (a >= 0) & (c >= 0)
            ai, ci = a[ok].tolist(), c[ok].tolist()
            k = len(ai)
            rows.extend(ai + ci + ai + ci)
            cols.extend(ai + ci + ci + ai)
            vals.extend([g] * (2 * k) + [-g] * (2 * k))

    def link(a, c, g):
        rows.extend([a, c, a, c])
        cols.extend([a, c, c, a])
        vals.extend([g, g, -g, -g])

    barrels = []
    for v in vias:
        x, y = M(v.GetPosition().x), M(v.GetPosition().y)
        r, c0 = cell(x, y)
        top, bot = std(v.TopLayer()), std(v.BottomLayer())
        i0, i1 = sorted((cu.index(top), cu.index(bot)))
        d = M(v.GetDrillValue())
        solid = filled_vias or v.GetViaType() == pcbnew.VIATYPE_MICROVIA
        area = math.pi * d * d / 4 if solid else math.pi * (d + plating) * plating
        prev = None
        for L in cu[i0:i1 + 1]:
            vn = nn
            nn += 1
            if L in node:
                rad = max(M(v.GetWidth(lids[cu.index(L)])), d + 2 * plating) / 2
                k_ = int(rad / grid) + 1
                for rr in range(max(0, r - k_), min(ny, r + k_ + 1)):
                    for cc in range(max(0, c0 - k_), min(nx, c0 + k_ + 1)):
                        if node[L][rr, cc] >= 0 and math.hypot(xs[cc] - x, ys[rr] - y) <= rad:
                            link(vn, int(node[L][rr, cc]), 1e4)
            if prev is not None:
                dz = max(abs(zl[L] - zl[prev[0]]), 1e-3)
                link(prev[1], vn, area / (RHO * dz))
                barrels.append((prev[1], vn, x, y, prev[0], L, area, area / (RHO * dz)))
            prev = (L, vn)

    G = sp.csr_matrix((vals, (rows, cols)), shape=(nn, nn))
    fixed: set[int] = set()
    for L, pp in src_pads:
        m = shapely.contains_xy(pp, XX, YY) & (node[L] >= 0)
        fixed.update(int(i) for i in node[L][m])
    if not fixed:
        raise ValueError(f"{rail.net}: source pads did not rasterise; try a finer grid")
    lp = []
    for name, L, pp in load_pads:
        m = shapely.contains_xy(pp, XX, YY) & (node[L] >= 0)
        ids = node[L][m]
        if ids.size == 0:
            r, c0 = cell(pp.centroid.x, pp.centroid.y)
            ids = np.array([node[L][r, c0]]) if node[L][r, c0] >= 0 else np.array([], dtype=np.int64)
        lp.append((name, ids))
    fx = np.array(sorted(fixed))
    _, lab = sp.csgraph.connected_components(G, directed=False)
    keep = np.isin(lab, np.unique(lab[fx]))
    reached = [(n, ids) for n, ids in lp if ids.size and keep[ids].any()]
    unreached = [n for n, ids in lp if not (ids.size and keep[ids].any())]
    if not reached:
        raise ValueError(f"{rail.net}: no load pad is connected to the source")
    I = np.zeros(nn)
    per = rail.current / len(reached)
    for _, ids in reached:
        ids = ids[keep[ids]]
        I[ids] -= per / ids.size
    free = np.setdiff1d(np.arange(nn), fx)
    free = free[keep[free]]
    V = np.zeros(nn)
    V[free] = spl.spsolve(G[free][:, free].tocsc(), I[free])

    drops = sorted(((-float(V[ids[keep[ids]]].mean()) * 1000.0, n) for n, ids in reached), reverse=True)
    sense_mv = None
    if sense_pads:
        sv = []
        for L, pp in sense_pads:
            m = shapely.contains_xy(pp, XX, YY) & (node[L] >= 0)
            sv.extend(V[node[L][m]].tolist())
        if sv:
            sense_mv = -float(np.mean(sv)) * 1000.0

    bl = []
    for a, c, x, y, La, Lb, area, g in barrels:
        bl.append(Barrel(abs(V[a] - V[c]) * g, round(x, 3), round(y, 3), show[La], show[Lb], ipc2221_internal_amps(area)))
    bl.sort(key=lambda q: -q.amps)

    loss: dict[tuple, float] = {}
    jmax, where = 0.0, ("", 0.0, 0.0)
    for L, idx in node.items():
        Vg = np.where(idx >= 0, V[np.maximum(idx, 0)], np.nan)
        for ax in (0, 1):
            dv = np.diff(Vg, axis=ax)
            P = np.nan_to_num(dv ** 2 * SIGMA * th[L])
            rr, cc = np.nonzero(P > 0)
            if rr.size:
                bx = np.floor(xs[cc]).astype(int)
                by = np.floor(ys[rr]).astype(int)
                keys, inv = np.unique(np.stack([bx, by], 1), axis=0, return_inverse=True)
                acc = np.zeros(len(keys))
                np.add.at(acc, inv.ravel(), P[rr, cc])
                for (kx, ky), pv in zip(keys, acc):
                    loss[(L, int(kx), int(ky))] = loss.get((L, int(kx), int(ky)), 0.0) + float(pv)
            J = np.nan_to_num(np.abs(dv)) * SIGMA / grid
            if J.size and J.max() > jmax:
                jmax = float(J.max())
                r_, c_ = np.unravel_index(int(np.argmax(J)), J.shape)
                where = (show[L], round(float(xs[c_]), 2), round(float(ys[r_]), 2))
    for a, c, x, y, La, Lb, area, g in barrels:
        k = ("via", round(x, 2), round(y, 2))
        loss[k] = loss.get(k, 0.0) + (V[a] - V[c]) ** 2 * g
    tot = sum(loss.values()) or 1.0
    share = sorted(((show.get(k[0], k[0]), float(k[1]), float(k[2]), v / tot) for k, v in loss.items()), key=lambda q: -q[3])[:12]

    if map_dir:
        _write_maps(map_dir, rail.net, {show[L]: v for L, v in node.items()}, V, grid)

    vnom = rail.vnom
    budget = None if (vnom is None or budget_pct is None) else budget_pct / 100.0 * vnom * 1000.0
    return Result(rail, loads, drops[0][0], drops[0][1], drops[len(drops) // 2][0], unreached, sense_mv,
                  len(vias), bl, jmax, where, share, budget)


def _write_maps(out: str, net: str, node: dict, V: np.ndarray, grid: float) -> None:
    """One PNG per layer: current density on a log colour scale (blue = low, red = high), grey = idle copper."""
    try:
        from PIL import Image
    except ImportError:   # optional dependency
        return
    os.makedirs(out, exist_ok=True)
    for L, idx in node.items():
        Vg = np.where(idx >= 0, V[np.maximum(idx, 0)], np.nan)
        gy, gx = np.gradient(np.nan_to_num(Vg))
        J = np.hypot(gx, gy) / grid * SIGMA
        img = np.zeros(idx.shape + (3,), np.uint8)
        img[idx >= 0] = (60, 60, 60)
        lv = np.clip(np.log10(J + 1e-9) / 2.5, 0, 1)
        hot = (idx >= 0) & (lv > 0)
        img[hot, 0] = (255 * lv[hot]).astype(np.uint8)
        img[hot, 1] = (255 * (1 - np.abs(lv[hot] - 0.5) * 2)).astype(np.uint8)
        img[hot, 2] = (255 * (1 - lv[hot])).astype(np.uint8)
        Image.fromarray(img).resize((idx.shape[1] * 2, idx.shape[0] * 2), Image.NEAREST).save(
            os.path.join(out, f"{net}-{L.replace('.', '_')}.png"))
