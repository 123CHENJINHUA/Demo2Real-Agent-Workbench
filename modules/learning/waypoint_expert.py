"""Scene-relative action search driven by offline rollout measurements."""

import numpy as np
from scipy.spatial.transform import Rotation

from modules.evaluation.action_trace import first_motion_fault, search_focus
from modules.motion_replay.kinematics import ToolKinematics


class WaypointExpert:
    def __init__(self, root, arms, model, phases, poses, robots, sizes):
        self.model = model
        self.times = model['times_s']
        self.phases = phases
        self.ends = self.times[-1] * np.asarray([phase['end_fraction'] for phase in phases])
        self.frames = {
            key: (np.asarray(pose['position_m']), Rotation.from_quat(np.asarray(pose['quaternion_wxyz'])[[1, 2, 3, 0]]))
            for key, pose in poses.items()
        }
        self.sizes = sizes
        self.roles = {robot['root_path']: robot['id'] for robot in robots}
        self.kinematics = [ToolKinematics(root / arm['urdf_path'], arm['base_pose']) for arm in arms]
        self.knots = []
        for phase, step in enumerate(phases):
            start = self.ends[phase - 1] if phase else 0.
            for point in step['waypoints']:
                fraction = point['fraction']
                time = self.ends[phase] if fraction == 1. else start + fraction * (self.ends[phase] - start)
                self.knots.append((phase, float(time), point))

    def command(self, offsets, corrections):
        joints = np.empty_like(self.model['reference_rad'])
        openings = np.empty_like(self.model['gripper_opening_m'])
        residuals = []
        initial_openings = self.model.get('recorded_gripper_m', self.model['gripper_opening_m'])[0]
        for arm_index, arm in enumerate(self.model['arms']):
            seed = self.model['reference_rad'][0, arm_index]
            gap = float(initial_openings[arm_index])
            targets = {0.: np.r_[seed, gap]}
            for phase, end in enumerate(self.ends):
                for index, (point_phase, at, point) in enumerate(self.knots):
                    if point_phase != phase or point['arm'] != arm:
                        continue
                    frame = point['relative_to']
                    pos = np.asarray(point['position_m']) + corrections[index, :3]
                    angles = point['orientation_xyz_deg']
                    orient = Rotation.from_euler('xyz', angles, degrees=True) if angles is not None else None
                    if frame is not None:
                        origin, rotation = self.frames[frame]
                        pos = origin + np.asarray(offsets.get(frame, [0., 0., 0.])) + rotation.apply(pos)
                        if orient is not None:
                            orient = rotation * orient
                    adjust = corrections[index, 3:6]
                    if np.any(adjust):
                        if orient is None:
                            orient = Rotation.from_matrix(self.kinematics[arm_index].tool(seed[None])[1][0])
                        orient = Rotation.from_rotvec(np.deg2rad(adjust)) * orient
                    seed, (distance, angle) = self.kinematics[arm_index].solve(
                        pos, orient, seed, self.model['limits_rad'][arm_index]
                    )
                    if distance > .006 or angle > 5.:
                        residuals.append({'waypoint': index, 'phase': phase, 'arm': arm, 'time_s': at,
                                          'position_error_m': distance, 'orientation_error_deg': angle})
                    if point['opening_m'] is not None:
                        gap = point['opening_m']
                    targets[at] = np.r_[seed, np.clip(gap + corrections[index, 6], 0., .09)]
                    gap = float(targets[at][-1])
                targets[float(end)] = np.r_[seed, gap]
            times = np.asarray(list(targets))
            values = np.asarray(list(targets.values()))
            right = np.clip(np.searchsorted(times, self.times, side='right'), 1, len(times) - 1)
            left = right - 1
            weight = ((self.times - times[left]) / (times[right] - times[left]))[:, None]
            blend = weight**2 * (3. - 2. * weight)
            trajectory = values[left] + blend * (values[right] - values[left])
            joints[:, arm_index], openings[:, arm_index] = trajectory[:, :-1], trajectory[:, -1]
        return joints, openings, residuals

    def observe(self, result, trace, residuals):
        """Retain the measured event used to choose the next change."""
        phase = search_focus([result], len(self.phases))
        fault = first_motion_fault(result)
        failures = [check for check in result['checks'] if check['phase'] == phase and check.get('evaluated')
                    and check['kind'] != 'unsupported' and not check['passed']]
        at = float(trace['times_s'][-1])
        if fault and fault['phase'] == phase:
            return fault | {'kind': fault.get('kind', 'early_fall'), 'waypoint_residuals': residuals[:8],
                            'tool_clearance_direction': self._clearance_direction(fault, trace)}
        if lost := result.get('lift_loss'):
            return lost | {'waypoint_residuals': residuals[:8]}
        if failures:
            return {'phase': phase, 'kind': failures[0]['kind'], 'time_s': at, 'check': failures[0],
                    'waypoint_residuals': residuals[:8]}
        if residuals and not any(check['passed'] for check in result['checks']):
            error = residuals[0]
            return {'phase': error['phase'], 'kind': 'waypoint_residual', 'time_s': error['time_s'],
                    'waypoint_residuals': residuals[:8]}
        return None

    def _clearance_direction(self, fault, trace):
        arm = self._contact_arm(fault)
        if arm is None or fault['object_id'] not in self.frames:
            return None
        frame = max(0, np.searchsorted(trace['times_s'], fault['time_s']) - 1)
        index = list(trace['object_ids']).index(fault['object_id'])
        ordinal = self.model['arms'].index(arm)
        fingers = trace['finger_m'][frame, 2 * ordinal:2 * ordinal + 2]
        if len(fingers) != 2:
            return None
        direction = fingers.mean(axis=0) - trace['object_m'][frame, index]
        if fault['object_id'] in self.sizes:
            rotation = Rotation.from_quat(trace['object_wxyz'][frame, index][[1, 2, 3, 0]])
            local = rotation.inv().apply(direction)
            half = np.asarray(self.sizes[fault['object_id']]) / 2.
            outside = local - np.clip(local, -half, half)
            if not outside.any():
                axis = np.argmin(half - np.abs(local))
                outside[axis] = np.copysign(1., local[axis])
            direction = rotation.apply(outside)
        norm = np.linalg.norm(direction)
        return (direction / norm).tolist() if norm > 1e-6 else None

    def _contact_arm(self, feedback):
        actor = (feedback.get('actors') or [''])[0]
        return next((arm for root, arm in self.roles.items() if actor.startswith(root + '/')), None)

    def propose(self, best, feedback, trial):
        """Perturb a target of the failed stage while keeping the best measured plan."""
        candidate = best.copy()
        phase, kind = feedback['phase'], feedback['kind']
        arm = self._contact_arm(feedback)
        # A failure to move the part can originate at the prior grasp or contact waypoint.
        grip_search = kind in ('rise', 'travel', 'early_fall', 'lift_loss') and trial % 3 != 0
        target_phase = max(0, phase - 1) if grip_search else phase
        points = [(i, at, point) for i, (step, at, point) in enumerate(self.knots)
                  if step <= target_phase and (arm is None or point['arm'] == arm)]
        if not points:
            points = [(i, at, point) for i, (_, at, point) in enumerate(self.knots)]
        indices = {i for i, _, _ in points}
        focus_phase = max(self.knots[i][0] for i in indices)
        points = [entry for entry in points if self.knots[entry[0]][0] == focus_phase]
        target_time = feedback['time_s'] if kind == 'unexpected_robot_contact' else self.ends[focus_phase]
        points.sort(key=lambda entry: abs(entry[1] - target_time))
        index, _, point = points[(trial // 6) % len(points)]
        change = np.zeros(7)
        amplitude = (.025, .05, .08, .12)[min(trial // 4, 3)]
        direction = feedback.get('tool_clearance_direction')
        check = feedback.get('check', {})
        error = check.get('correction_world_m')
        angular_error = check.get('correction_world_deg')
        if kind == 'unexpected_robot_contact' and direction and trial % 3 != 0:
            change[:3] = amplitude * np.asarray(direction)
        elif kind == 'unexpected_robot_contact' and trial % 2:
            change[3 + (trial // 3) % 3] = 15. * (-1 if trial % 4 == 3 else 1)
        elif error and trial % 3 != 0:
            change[:3] = np.clip(error, -.12, .12)
        elif kind == 'gripper_gap':
            change[6] = amplitude / 2.
        elif kind == 'orientation':
            if angular_error and trial % 3 != 0:
                change[3:6] = np.clip(angular_error, -20., 20.)
            else:
                change[3 + (trial // 2) % 3] = 12. * (-1 if trial % 2 else 1)
        elif kind == 'rise' and not grip_search:
            change[2] = min(.12, max(.01, check['threshold'] - check['measured'])) if check.get('measured') is not None else amplitude
        else:
            axis, sign = divmod(trial % 6, 2)
            change[axis] = amplitude * (-1 if sign else 1)
            if grip_search and point['opening_m'] is not None:
                change[6] = .006 if kind == 'lift_loss' and trial % 2 else -.006
        if (frame := point['relative_to']) is not None:
            change[:3] = self.frames[frame][1].inv().apply(change[:3])
        bound = np.array([.22, .22, .22, 45., 45., 45., .05])
        candidate[index] = np.clip(candidate[index] + change, -bound, bound)
        change = candidate[index] - best[index]
        return candidate, {'phase': focus_phase, 'waypoint': index, 'arm': point['arm'], 'reason': kind,
                           'translation_m': change[:3].tolist(), 'rotation_xyz_deg': change[3:6].tolist(),
                           'opening_delta_m': float(change[6])}
