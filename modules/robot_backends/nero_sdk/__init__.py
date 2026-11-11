"""Only business module permitted to import SDK; importing does not connect."""
from pathlib import Path

class NeroRobotBackend:
    mode="disconnected"
    def __init__(self, root):self.root=Path(root)
    def capabilities(self):
        return {"sdk_present":(self.root/"vendor/pyAgxArm/pyAgxArm").is_dir(),"passive_sdk_verified":False,"motion_enabled":False}
    def connect(self,*args,**kwargs):
        raise PermissionError("SDK connect has not been verified passive; use a separately reviewed hardware session")
    def send_joint_target(self,*args,**kwargs):
        raise PermissionError("Real hardware motion is not authorized or commissioned")
    def hold(self):
        raise PermissionError("No verified hardware hold protocol; no SDK command was sent")
