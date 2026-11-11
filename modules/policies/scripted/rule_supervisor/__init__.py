from contracts.policies import ObservationPacket, PolicyDescriptor, SkillDecision, OBSERVATION, SUPERVISOR_ACTION

class RuleSupervisor:
    def describe(self):
        return PolicyDescriptor("rule_supervisor_v1", "supervisor", OBSERVATION, SUPERVISOR_ACTION, "1")
    def reset(self, epoch):
        self.epoch = epoch
    def predict(self, observation):
        observation.validate()
        if observation.epoch != getattr(self,"epoch",None):
            raise ValueError("policy must reset when epoch changes")
        if len(observation.features) != 8:
            return SkillDecision("observe", "需要完整 supervisor-features/v1 的8维相机/机器人特征")
        confidence, visible, age, held, displacement, joint_speed, reference_error, target_visible = observation.features
        if age > .25:
            return SkillDecision("wait", "相机观测已过期")
        if confidence < .5 or visible < .5 or target_visible < .5:
            return SkillDecision("observe", "目标观测不足，重新获取")
        if joint_speed > 1.2:
            return SkillDecision("abort", "关节速度超出当前已支持范围")
        if held < .5 and observation.skill in ("transport", "align", "insert"):
            return SkillDecision("regrasp", "相机/夹爪状态表明持物丢失，需重新定位和规划")
        if displacement > .05 or reference_error > .08:
            return SkillDecision("wait", "扰动超出局部修正范围，需重新规划")
        return SkillDecision("follow_reference", "观测有效，允许参考策略继续提出运动")
