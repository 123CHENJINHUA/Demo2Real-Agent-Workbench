"""Phase recovery messages; native fields only, no simulator/device imports."""
from dataclasses import dataclass

@dataclass(frozen=True)
class PhaseState:
 time_s: float
 active_insertion: bool
 observed: bool
 complete: bool
 held: tuple[bool,bool]
 suspicious: tuple[bool,bool]
 tabletop: tuple[bool,bool]
 supported_pair: bool
 depth_m: float

@dataclass(frozen=True)
class PhaseDecision:
 mode: str
 reason: str
 arm: int | None = None
