"""Build a 4-layer cross-check board for kicad-irdrop vs padne.

Usage: KiCad-python make_xcheck_board.py TEMPLATE.kicad_pcb OUT_DIR [NECK_WIDTH_MM]   (default 0.5; we ran 0.5 and 3.0)

Geometry (mm): source pad J1 on F.Cu -> 0.5 mm track -> 4-via cluster -> notched PWR pour on In2.Cu
-> 3x3 load pads U1 (2 mm pitch) on F.Cu, each with a dogbone via down to the pour. In1.Cu is a full GND plane.
G1 is a lone GND pad, only used as padne's voltage reference (no current flows through it).
"""
import sys
import pcbnew

tpl, out = sys.argv[1], sys.argv[2]
neck = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
b = pcbnew.LoadBoard(tpl)
for item in list(b.GetFootprints()) + list(b.GetTracks()) + list(b.Zones()) + list(b.GetDrawings()):
    b.Remove(item)

mm = pcbnew.FromMM
pt = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))


def net(name):
    n = b.FindNet(name)
    if n is None:
        n = pcbnew.NETINFO_ITEM(b, name)
        b.Add(n)
    return n


PWR, GND = net("PWR"), net("GND")

# board outline
for (x0, y0), (x1, y1) in [((0, 0), (40, 0)), ((40, 0), (40, 30)), ((40, 30), (0, 30)), ((0, 30), (0, 0))]:
    s = pcbnew.PCB_SHAPE(b)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pt(x0, y0)); s.SetEnd(pt(x1, y1)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1))
    b.Add(s)


def footprint(ref, pads):
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference(ref)
    fp.SetValue(ref)
    b.Add(fp)
    for num, x, y, size, n in pads:
        p = pcbnew.PAD(fp)
        p.SetNumber(num)
        p.SetShape(pcbnew.PAD_SHAPE_RECTANGLE if size > 1 else pcbnew.PAD_SHAPE_CIRCLE)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        p.SetLayerSet(ls)
        p.SetSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        p.SetPosition(pt(x, y))
        p.SetNet(n)
        fp.Add(p)
    return fp


footprint("J1", [("1", 4, 15, 2.0, PWR)])
footprint("G1", [("1", 4, 4, 1.5, GND)])
grid = [(30 + dx, 15 + dy) for dy in (-2, 0, 2) for dx in (-2, 0, 2)]
footprint("U1", [(str(i + 1), x, y, 0.5, PWR) for i, (x, y) in enumerate(grid)])


def track(x0, y0, x1, y1, w, layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pt(x0, y0)); t.SetEnd(pt(x1, y1)); t.SetWidth(mm(w)); t.SetLayer(layer); t.SetNet(PWR)
    b.Add(t)


def via(x, y):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pt(x, y)); v.SetDrill(mm(0.3)); v.SetWidth(mm(0.6)); v.SetNet(PWR)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    b.Add(v)


# source -> 0.5 mm neck -> 1.5 mm landing with a 2x2 via cluster
track(4, 15, 9, 15, neck)
track(9, 15, 11, 15, 1.5)
for x, y in [(10, 14.5), (10, 15.5), (11, 14.5), (11, 15.5)]:
    via(x, y)
# one via per load pad, 0.8 mm to the right on a short 0.3 mm track (a dogbone)
for x, y in grid:
    track(x, y, x + 0.8, y, 0.3)
    via(x + 0.8, y)


def zone(layer, n, pts, prio=0):
    z = pcbnew.ZONE(b)
    z.SetLayer(layer); z.SetNet(n); z.SetAssignedPriority(prio)
    z.SetLocalClearance(mm(0.2)); z.SetMinThickness(mm(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    ol = pcbnew.SHAPE_POLY_SET(); ol.NewOutline()
    for x, y in pts:
        ol.Append(mm(x), mm(y))
    z.SetOutline(ol)
    b.Add(z)


# PWR pour on In2 with a notch the current has to go around
zone(pcbnew.In2_Cu, PWR, [(8, 8), (36, 8), (36, 22), (22, 22), (22, 12), (18, 12), (18, 22), (8, 22)])
zone(pcbnew.In1_Cu, GND, [(1, 1), (39, 1), (39, 29), (1, 29)])

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(f"{out}/xcheck.kicad_pcb", b)
print("saved", out)
