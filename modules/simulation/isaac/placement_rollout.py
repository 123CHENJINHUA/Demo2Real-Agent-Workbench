"""Adapter for the explicitly selected rigid panel placement experiment."""
import numpy as np

from .motion_rollout import MotionRollouts


class PlacementRollouts(MotionRollouts):
    def __init__(self, scene_path, prepared_path, manifest, experiment, app):
        self.roles = experiment['objects']
        super().__init__(scene_path,prepared_path,manifest,app,fixed_ids=(self.roles['source'],self.roles['goal']))

    def execute(self, case, model, commands):
        source_offset = np.r_[case['source_xy_m'],0.]
        trace = super().execute({self.roles['panel']:source_offset,self.roles['source']:source_offset,
                                 self.roles['goal']:np.r_[case['goal_xy_m'],0.]},model,commands)
        indices = trace['object_ids'].tolist()
        panel,source,goal = (indices.index(self.roles[key]) for key in ('panel','source','goal'))
        return {'times_s':trace['times_s'],'panel_m':trace['object_m'][:,panel],'panel_wxyz':trace['object_wxyz'][:,panel],
                'finger_m':trace['finger_m'],'actual_joints':trace['actual_joints'],
                'panel_speed_m_s':trace['object_speed_m_s'][:,panel],'source_m':trace['object_m'][0,source],
                'goal_m':trace['object_m'][0,goal],'goal_wxyz':trace['object_wxyz'][0,goal]}
