"""SI units, monotonic wall time and explicit actor-visible observations."""
from dataclasses import dataclass, field
from typing import Protocol, Literal
import hashlib
import json
import math

OBSERVATION = "robot-camera-state/v1"
SUPERVISOR_ACTION = "skill-decision/v1"
MOTION_ACTION = "joint-position/v1"

@dataclass(frozen=True)
class ObservationPacket:
    sequence: int
    captured_at: float
    epoch: str
    joints_rad: tuple[float, ...]
    gripper_m: tuple[float, ...]
    objects: tuple[dict, ...] = ()
    features: tuple[float, ...] = ()
    skill: str = "observe"
    contract: str = OBSERVATION
    source: str = "camera_estimate"

    def validate(self):
        if self.contract != OBSERVATION:
            raise ValueError("incompatible observation contract")
        if self.source not in ("camera_estimate", "mock"):
            raise ValueError("actor observations must not contain simulator truth")
        if not self.epoch or self.sequence < 0 or not math.isfinite(self.captured_at):
            raise ValueError("invalid observation identity/time")
        if not all(math.isfinite(v) for v in (*self.joints_rad, *self.gripper_m, *self.features)):
            raise ValueError("nonfinite observation")

@dataclass(frozen=True)
class SkillDecision:
    skill: str
    reason: str
    contract: str = SUPERVISOR_ACTION

@dataclass(frozen=True)
class MotionProposal:
    joints_rad: tuple[float, ...]
    epoch: str
    observation_sequence: int
    created_at: float
    valid_until: float
    frame: str = "robot_joint"
    contract: str = MOTION_ACTION

@dataclass(frozen=True)
class PolicyDescriptor:
    plugin_id: str
    role: Literal["supervisor", "motion"]
    observation_contract: str
    action_contract: str
    version: str
    hardware_verified: bool = False

class SupervisorPolicy(Protocol):
    def describe(self) -> PolicyDescriptor: ...
    def reset(self, epoch: str) -> None: ...
    def predict(self, observation: ObservationPacket) -> SkillDecision: ...

class MotionPolicy(Protocol):
    def describe(self) -> PolicyDescriptor: ...
    def reset(self, epoch: str) -> None: ...
    def predict(self, observation: ObservationPacket) -> MotionProposal | None: ...

def dependency_signature(versions: dict) -> str:
    required = {"task", "observation", "action", "asset", "calibration", "evaluation"}
    if set(versions) != required or any(not str(v) for v in versions.values()):
        raise ValueError("complete frozen dependency versions required")
    return hashlib.sha256(json.dumps(versions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
