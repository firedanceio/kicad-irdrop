from kicad_irdrop.stackup import read_stackup

BOARD = """(kicad_pcb (version 20240108) (setup (stackup
  (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.1))
  (layer "In1.Cu" (type "copper") (thickness 0.018))
  (layer "dielectric 2" (type "core") (thickness 1.2) (epsilon_r 4.5) (addsublayer) (thickness 0.1) (epsilon_r 4.5))
  (layer "In2.Cu" (type "copper") (thickness 0.018))
  (layer "dielectric 3" (type "prepreg") (thickness 0.1))
  (layer "B.Cu" (type "copper") (thickness 0.035))
)))"""


def test_reads_copper_and_dielectrics(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text(BOARD)
    st = read_stackup(str(p))
    assert [l.name for l in st.copper] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert st.copper_thickness("In1.Cu") == 0.018
    diel = [l for l in st.layers if l.kind != "copper"]
    assert diel[1].thickness == 1.3 and diel[1].epsilon_r == 4.5     # sub-layers summed
    z = st.z_mid()
    assert z["F.Cu"] == 0.0175
    assert abs(z["In1.Cu"] - (0.035 + 0.1 + 0.009)) < 1e-9


def test_missing_stackup_is_assumed(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text("(kicad_pcb (version 1))")
    st = read_stackup(str(p), ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
    assert len(st.copper) == 4 and st.copper_thickness("F.Cu") == 0.035
    assert abs(sum(l.thickness for l in st.layers) - 1.6) < 1e-6
