"""Evidence scope must remain explicit across simulation and hardware."""
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Incident:
    incident_id: str
    run_id: str
    stage: str
    category: str
    symptom: str
    evidence: tuple[str, ...]
    scope: Literal["source_data", "simulation", "hardware", "infrastructure"]
    root_cause_status: Literal["unknown", "hypothesis", "confirmed"] = "unknown"
    resolved: bool = False
