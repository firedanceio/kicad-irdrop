"""kicad-irdrop: DC IR-drop on a KiCad board's real copper, vias included."""
from .solver import Rail, Result, solve
from .stackup import Stackup, read_stackup

__all__ = ["Rail", "Result", "solve", "Stackup", "read_stackup"]
__version__ = "0.1.0"
