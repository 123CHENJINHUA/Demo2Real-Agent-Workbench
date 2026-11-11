class MockRobot:
    mode="mock"
    def __init__(self,joints):self.joints=tuple(joints);self.commands=[];self.held=False
    def send_joint_target(self,joints):self.joints=tuple(joints);self.commands.append(self.joints)
    def hold(self):self.held=True

class ShadowRobot(MockRobot):
    mode="shadow"
    def send_joint_target(self,joints):self.commands.append(tuple(joints))
