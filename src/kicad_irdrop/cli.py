"""kicad-irdrop command line."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .solver import Rail, solve
from .stackup import read_stackup

EPILOG = """rail spec: NET:SOURCE:AMPS[:LOAD1+LOAD2[:SENSE[:VNOM]]]
  NET     the power net, e.g. VDD_CORE
  SOURCE  reference of the part that feeds it (the buck's inductor, an LDO)
  AMPS    total load current to solve for
  LOADS   load part references joined with '+' (default U1); each load pad sinks an equal share
  SENSE   reference of the regulator's feedback/sense part (optional): the drop the load sees is measured from it
  VNOM    nominal rail voltage (optional): enables the --budget-pct check

example:
  kicad-irdrop board.kicad_pcb --rail VDD_CORE:L3:3:U1:R12:0.9 --rail VCC_DDR:L4:1:U1+U5::1.1 --maps out/
Run it with KiCad's Python (it imports pcbnew), e.g. on macOS
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 -m kicad_irdrop ..."""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kicad-irdrop", description="DC IR-drop of power nets on a KiCad board's real copper.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help=".kicad_pcb file (zones should be filled)")
    ap.add_argument("--rail", action="append", required=True, help="rail spec, repeatable (see below)")
    ap.add_argument("--grid", type=float, default=0.05, help="raster grid in mm (default 0.05; halve it to check convergence)")
    ap.add_argument("--plating", type=float, default=0.020, help="via barrel plating thickness in mm (default 0.020)")
    ap.add_argument("--filled-vias", action="store_true", help="treat every via as copper-filled")
    ap.add_argument("--budget-pct", type=float, default=2.0, help="DC drop budget as %% of VNOM (default 2)")
    ap.add_argument("--maps", metavar="DIR", help="write current-density PNGs per net and layer (needs Pillow)")
    ap.add_argument("--json", metavar="FILE", help="write all results as JSON")
    ap.add_argument("--version", action="version", version=f"kicad-irdrop {__version__}")
    a = ap.parse_args(argv)
    try:
        import pcbnew
    except ImportError:
        print("kicad-irdrop needs KiCad's pcbnew module: run it with the Python that ships with KiCad", file=sys.stderr)
        return 2
    board, why = None, ""
    try:
        board = pcbnew.LoadBoard(a.board)
    except Exception as e:                       # some KiCad versions raise, others return None
        why = f": {e}"
    if board is None:
        print(f"could not load {a.board}{why}", file=sys.stderr)
        return 2
    cu = [pcbnew.BOARD.GetStandardLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    stack = read_stackup(a.board, cu)
    out, worst = [], 0
    for spec in a.rail:
        rail = Rail.parse(spec)
        try:
            r = solve(board, stack, rail, a.grid, a.plating, a.filled_vias, a.budget_pct, a.maps)
        except ValueError as e:
            print(f"ERROR {e}")
            worst = max(worst, 2)
            continue
        out.append(r.as_dict())
        line = (f"{rail.net}: {rail.current:g} A from {rail.source} to {len(r.loads) and '+'.join(r.loads)} | worst {r.worst_mv:.1f} mV at "
                f"{r.worst_pad}, median {r.median_mv:.1f} mV, R_eff {r.r_eff_mohm:.2f} mOhm")
        if r.sense_mv is not None:
            line += f" | sensed at {rail.sense}: the load sees {r.seen_mv:.1f} mV"
        if r.budget_mv is not None:
            line += f" | budget {r.budget_mv:.1f} mV: {'OVER' if r.over_budget else 'ok'}"
            worst = max(worst, 1 if r.over_budget else 0)
        print(line)
        if r.unreached:
            print(f"  not connected to the source: {', '.join(r.unreached)}")
        over = sum(1 for q in r.barrels if q.amps > q.rating)
        print(f"  vias {r.vias}; {over} barrel segments over their IPC-2221 rating; busiest: " +
              ", ".join(f"{q.amps:.2f} A @({q.x},{q.y}) {q.layer_a}-{q.layer_b}" for q in r.barrels[:3]))
        print(f"  peak current density {r.peak_density:.0f} A/mm2 on {r.peak_where[0]} at ({r.peak_where[1]},{r.peak_where[2]})")
        print("  loss by location: " + ", ".join(f"{w}@{x:g},{y:g} {100 * f:.0f}%" for w, x, y, f in r.loss_share[:6]))
    if a.json:
        json.dump({"board": a.board, "grid_mm": a.grid, "plating_mm": a.plating, "rails": out}, open(a.json, "w"), indent=1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
