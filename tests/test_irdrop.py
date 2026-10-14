"""Tests on synthetic boards built with pcbnew; skipped where KiCad's Python is not available."""
import math
import os

import pytest

pcbnew = pytest.importorskip("pcbnew")

from kicad_irdrop import Rail, read_stackup, solve
from kicad_irdrop.solver import RHO

mm = pcbnew.FromMM


def _pad_part(b, ref, x, y, net, size=1.0, layer="F.Cu"):
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference(ref)
    fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    b.Add(fp)
    p = pcbnew.PAD(fp)
    p.SetNumber("1")
    p.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
    p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    ls = pcbnew.LSET()
    ls.AddLayer(b.GetLayerID(layer))
    p.SetLayerSet(ls)
    p.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    p.SetNet(net)
    fp.Add(p)
    return fp


def _track(b, net, a, c, w, layer="F.Cu"):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
    t.SetEnd(pcbnew.VECTOR2I(mm(c[0]), mm(c[1])))
    t.SetWidth(mm(w))
    t.SetLayer(b.GetLayerID(layer))
    t.SetNet(net)
    b.Add(t)


def _board(tmp_path, build):
    b = pcbnew.BOARD()
    b.Add(pcbnew.NETINFO_ITEM(b, "VRAIL", 1))
    net = b.FindNet("VRAIL")
    build(b, net)
    path = os.path.join(tmp_path, "t.kicad_pcb")
    b.Save(path)
    return pcbnew.LoadBoard(path), path


def test_straight_track_matches_hand_calculation(tmp_path):
    L, w = 20.0, 0.5

    def build(b, net):
        _pad_part(b, "L1", 0, 0, net)
        _pad_part(b, "U1", L, 0, net)
        _track(b, net, (0, 0), (L, 0), w)

    b, path = _board(str(tmp_path), build)
    st = read_stackup(path, ["F.Cu", "B.Cu"])
    t = st.copper_thickness("F.Cu")
    r = solve(b, st, Rail("VRAIL", "L1", 1.0, ["U1"]), grid=0.025, budget_pct=None)
    # pad to pad: the 1 mm pads cover 0.5 mm of track at each end
    expect_mohm = RHO * (L - 1.0) / (w * t) * 1000
    assert r.r_eff_mohm == pytest.approx(expect_mohm, rel=0.08)
    assert not r.unreached


def test_via_barrel_adds_its_resistance(tmp_path):
    def build(b, net):
        _pad_part(b, "L1", 0, 0, net)
        _pad_part(b, "U1", 10, 0, net, layer="B.Cu")
        _track(b, net, (0, 0), (5, 0), 2.0)
        _track(b, net, (5, 0), (10, 0), 2.0, layer="B.Cu")
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(mm(5), 0))
        v.SetWidth(mm(0.6))
        v.SetDrill(mm(0.3))
        v.SetNet(net)
        b.Add(v)

    b, path = _board(str(tmp_path), build)
    st = read_stackup(path, ["F.Cu", "B.Cu"])
    one = solve(b, st, Rail("VRAIL", "L1", 1.0, ["U1"]), budget_pct=None)
    zl = st.z_mid()
    dz = zl["B.Cu"] - zl["F.Cu"]
    barrel = RHO * dz / (math.pi * (0.3 + 0.02) * 0.02) * 1000
    assert one.vias == 1
    assert one.barrels[0].amps == pytest.approx(1.0, rel=0.02)          # all the current goes through the one via
    assert one.r_eff_mohm > barrel                                         # the barrel is in series
    filled = solve(b, st, Rail("VRAIL", "L1", 1.0, ["U1"]), filled_vias=True, budget_pct=None)
    assert filled.r_eff_mohm < one.r_eff_mohm


def test_sense_point_and_budget(tmp_path):
    def build(b, net):
        _pad_part(b, "L1", 0, 0, net)
        _pad_part(b, "R9", 10, 0, net, size=0.5)       # sense resistor half way
        _pad_part(b, "U1", 20, 0, net)
        _track(b, net, (0, 0), (20, 0), 0.3)

    b, path = _board(str(tmp_path), build)
    st = read_stackup(path, ["F.Cu", "B.Cu"])
    r = solve(b, st, Rail("VRAIL", "L1", 2.0, ["U1"], sense="R9", vnom=1.0), budget_pct=2.0)
    assert r.sense_mv is not None and 0.3 * r.worst_mv < r.sense_mv < 0.7 * r.worst_mv
    assert r.seen_mv == pytest.approx(r.worst_mv - r.sense_mv)
    assert r.budget_mv == pytest.approx(20.0)


def test_unconnected_load_is_reported(tmp_path):
    def build(b, net):
        _pad_part(b, "L1", 0, 0, net)
        _pad_part(b, "U1", 10, 0, net)
        _pad_part(b, "U2", 30, 0, net)                 # no copper to it
        _track(b, net, (0, 0), (10, 0), 0.5)

    b, path = _board(str(tmp_path), build)
    r = solve(b, read_stackup(path, ["F.Cu", "B.Cu"]), Rail("VRAIL", "L1", 1.0, ["U1", "U2"]), budget_pct=None)
    assert r.unreached == ["U2.1"]


def test_rail_spec_parsing():
    r = Rail.parse("VDD:L3:2.5:U1+U5:R12:0.9")
    assert (r.net, r.source, r.current, r.loads, r.sense, r.vnom) == ("VDD", "L3", 2.5, ["U1", "U5"], "R12", 0.9)
    r = Rail.parse("VDD:L3:1")
    assert r.loads == [] and r.sense is None and r.vnom is None
