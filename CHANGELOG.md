# Changelog

## 0.1.0 (unreleased)
- First release: DC IR-drop solve on rasterised copper with via barrels, sense points, per-via current against
  IPC-2221, loss by location, current-density PNG maps, JSON output; renamed layers handled (stack-up looked up by KiCad's standard names).
- A board that cannot be loaded is reported with exit status 2 instead of a traceback.
- Docs: a second worked example (Antmicro's 12-layer DDR4 tester) and a cross-check against padne on a purpose-built
  board (docs/crosscheck).
