# Validation

What we checked before release, why, and what happened. Run on 2026-10-14 with KiCad 10.0.6 on macOS.

## What was needed

kicad-irdrop came out of our own board review, so before sharing it we needed to know three things: that it runs on
boards we didn't design, that its answer settles as the grid gets finer, and that an independent solver gets the same
answer on the same copper.

## 1. Unit tests

`pytest`: 7 passed. These include a straight track against the hand calculation R = rho L / (w t) (within 8 %), a single
via carrying all of the current, sense-point and budget arithmetic, and an unconnected load being reported.

## 2. Open-source boards

| Board | Rail | Result | Grid check |
|---|---|---|---|
| [Antmicro LPDDR4 test board](https://github.com/antmicro/lpddr4-test-board) (8 layers, commit 5c8b5d3) | 1V0_SYS, R147 to U3, 2 A | 5.3 mV worst, 2.67 mOhm | about 1 % at half the grid |
| [Antmicro DDR4 RDIMM tester](https://github.com/antmicro/data-center-dram-tester) (12 layers, commit e7c85eb) | 1V0_SYS, R204 to U15, 3 A | 17.0 mV worst, 5.67 mOhm | 5.58 mOhm at 0.025 mm (1.6 %) |

Both are worked through in the README. On the DDR4 tester we first passed the net tie NT14 as the sense point and
saw that it is a tap on the power monitor's current-sense resistor, not the regulator's feedback. The sense option is
for the regulator's feedback point, so the example leaves it out.

## 3. Cross-check against padne

[padne](https://github.com/atx/padne) 0.3 is an independent finite-element DC solver for KiCad. We ran its Linux
release binary in a Docker container with no network access.

On both Antmicro boards padne stopped at its meshing step ("Non-manifold mesh"), with its default settings and with a
finer mesh angle, so we built a 4-layer test board both tools can solve: a source pad, a track, a 2x2 via cluster, a
notched pour on an inner layer and a 3x3 load. Same copper conductivity (5.8e7 S/m) and via plating (25 um) in both.

| Case | padne worst / median | kicad-irdrop worst / median | Difference |
|---|---|---|---|
| 0.5 mm neck (track dominates) | 12.23 / 12.18 mV | 12.21 / 12.17 mV | under 0.5 % |
| 3.0 mm neck (vias and pour dominate) | 6.01 / 5.96 mV | 6.1 / 6.0 mV | about 2.5 % |

Both put the peak current density at the notch corner, and their maps of the pour match (side by side in docs/crosscheck). Getting padne to read the board took two workarounds, both on
padne's input side: its bundled KiCad doesn't read the KiCad 10 file format, so `docs/crosscheck/to_kicad8.py` writes
the board in KiCad 8 syntax; and padne skips a load pad whose centre sits on a via hole, so the test board uses short
tracks to the vias. Everything needed to repeat it is in [docs/crosscheck](docs/crosscheck/README.md).

## 4. Fixed along the way

- A board file that couldn't be loaded ended in a Python traceback. It is now reported with exit status 2.
