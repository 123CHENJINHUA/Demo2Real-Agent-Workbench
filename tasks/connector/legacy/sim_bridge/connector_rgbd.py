"""Calibrated wrist RGBD. This acquisition layer has no object handles or labels.

All poses in a frame are reconstructed from measured joints and fixed calibration.
Depth is optical Z in metres, registered to the RGB pixels (not camera range).
"""
from pathlib import Path
import json
import numpy as np

from sim_bridge.connector_calibration import calibration


class WristRGBD:
    def __init__(self, arms, tool, output, record_interval=0., clock=None,
                 depth_noise=0.,depth_dropout=0.,min_depth=.005,max_depth=10.,seed=0):
        import omni.replicator.core as rep
        import omni.graph.core as og
        from collections import deque
        from isaacsim.core.nodes.bindings import _isaacsim_core_nodes
        self.K, self.T_gripper_camera = calibration()
        self.tool = tool
        self.clock = clock
        self.clock_origin = 0.
        self.rng=np.random.default_rng(seed+431)
        self.depth_settings=dict(gaussian_std_m=depth_noise,independent_dropout=depth_dropout,
                                 min_depth_m=min_depth,max_depth_m=max_depth,
                                 calibrated_hardware_noise_model=False)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.frames = None
        self.consumer = None
        self.sequence = 0
        self.products = []
        self.annotators = []
        self.paths = []
        self.graph_controller = og.Controller
        self.history = deque(maxlen=480)
        self.record_interval = record_interval
        self.last_record = -1e6
        self.core_nodes = _isaacsim_core_nodes.acquire_interface()
        self.audit = (self.output/'timing_audit.jsonl').open('w', buffering=1)
        for arm in arms:
            path = arm['root']+'/gripper_base/CalibratedCamera/Color'
            product = rep.create.render_product(path, (640, 480))
            color = rep.AnnotatorRegistry.get_annotator('rgb')
            depth = rep.AnnotatorRegistry.get_annotator('distance_to_image_plane')
            reference = rep.AnnotatorRegistry.get_annotator('ReferenceTime')
            camera_params = rep.AnnotatorRegistry.get_annotator('CameraParams')
            color.attach(product)
            depth.attach(product)
            reference.attach(product)
            camera_params.attach(product)
            self.products.append(product)
            self.annotators.append((color, depth, reference, camera_params))
            self.paths.append(path)
        self.graph = self.graph_controller.graph('/Render/PostProcess/SDGPipeline')
        (self.output/'acquisition.json').write_text(json.dumps(dict(
            camera_paths=self.paths, resolution=[640, 480], K=self.K.tolist(),
            T_gripper_camera_optical=self.T_gripper_camera.tolist(),
            depth_units='metres', depth_convention='optical Z',
            extrinsic_source='measured joint FK + fixed base and hand-eye calibration',
            depth_settings=self.depth_settings,
            object_handles_available=False, semantic_labels_available=False), indent=2))

    def record_robot_state(self, time):
        if not self.history and self.clock is not None:
            # Sensor/physics initialization can advance Kit before our trial
            # clock starts. Convert the two clock domains using robot time.
            self.clock_origin = float(self.clock())-float(time)
        self.history.append((float(time), np.array([self.tool(a) for a in range(2)])))

    def tools_at(self, time):
        from scipy.spatial.transform import Rotation, Slerp
        entries = list(self.history)
        if not entries or time<entries[0][0]-1e-5 or time>entries[-1][0]+1e-5:
            raise ValueError(f'Render time {time} outside measured-joint history')
        index = int(np.searchsorted([t for t,_ in entries],time))
        if index==0:return entries[0][1].copy()
        if index==len(entries):return entries[-1][1].copy()
        t0,p0=entries[index-1];t1,p1=entries[index]
        u=np.clip((time-t0)/max(1e-9,t1-t0),0,1)
        poses=p0.copy()
        poses[:,:3,3]=(1-u)*p0[:,:3,3]+u*p1[:,:3,3]
        for a in range(2):
            poses[a,:3,:3]=Slerp([0.,1.],Rotation.from_matrix(np.array([p0[a,:3,:3],p1[a,:3,:3]])))([u]).as_matrix()[0]
        return poses

    def update(self, time):
        self.graph_controller.evaluate_sync(graph_id=self.graph)
        frames = []
        audit = dict(delivery_time=float(time), cameras=[])
        for arm, (color, distance, reference, camera_params) in enumerate(self.annotators):
            rgb = np.asarray(color.get_data())
            depth = np.asarray(distance.get_data())
            if rgb.shape[:2] != (480, 640) or depth.shape != (480, 640):
                return False
            depth = depth.astype(np.float32, copy=True)
            valid=np.isfinite(depth)&(depth>=self.depth_settings['min_depth_m'])&(depth<=self.depth_settings['max_depth_m'])
            if self.depth_settings['gaussian_std_m']:
                depth[valid]+=self.rng.normal(0,self.depth_settings['gaussian_std_m'],int(valid.sum())).astype(np.float32)
            if self.depth_settings['independent_dropout']:
                valid &= self.rng.random(depth.shape)>=self.depth_settings['independent_dropout']
            valid &= (depth>=self.depth_settings['min_depth_m'])&(depth<=self.depth_settings['max_depth_m'])
            depth[~valid] = 0
            ref=reference.get_data()
            reference_time=float(self.core_nodes.get_sim_time_at_time((ref['referenceTimeNumerator'],ref['referenceTimeDenominator'])))
            capture_time=reference_time-self.clock_origin
            try: matched=self.tools_at(capture_time)[arm]
            except ValueError:
                self.audit.write(json.dumps(dict(delivery_time=float(time), capture_time=capture_time,
                    reference=dict(ref), clock_origin=self.clock_origin,
                    robot_clock=None if self.clock is None else float(self.clock()),
                    rejected='render_time_outside_joint_history',
                    history_range=[self.history[0][0],self.history[-1][0]]))+'\n')
                return False
            frames.append(dict(rgb=rgb[:, :, :3].astype(np.uint8, copy=True), depth_m=depth,
                               K=self.K.copy(), time=capture_time, delivery_time=float(time), sequence=self.sequence,
                               T_world_camera=matched@self.T_gripper_camera))
            params=camera_params.get_data()
            audit['cameras'].append(dict(capture_time=capture_time,
                reference_time=reference_time, clock_origin=self.clock_origin,
                T_world_camera_fk=(matched@self.T_gripper_camera).tolist(),
                T_world_camera_fk_delivery=(self.tool(arm)@self.T_gripper_camera).tolist(),
                render_view_matrix=np.asarray(params['cameraViewTransform']).reshape(4,4).tolist()))
        self.audit.write(json.dumps(audit)+'\n')
        self.frames = frames
        self.sequence += 1
        if self.record_interval and time-self.last_record>=self.record_interval:
            self.last_record=time
            self.save(self.output/f'recorded/{self.sequence:05d}')
        if self.consumer is not None:
            self.consumer(self.frames)
        return True

    def save(self, directory):
        from PIL import Image
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if self.frames is None:
            raise RuntimeError('No valid rendered wrist RGBD frames')
        for a, frame in enumerate(self.frames):
            np.savez_compressed(directory/f'camera_{a}.npz', **frame)
            Image.fromarray(frame['rgb']).save(directory/f'camera_{a}_rgb.png')
            d = frame['depth_m']
            valid = d > 0
            low, high = np.percentile(d[valid], [5, 95]) if np.any(valid) else (0., 1.)
            gray = np.where(valid, 255*(1-np.clip((d-low)/max(.01, high-low), 0, 1)), 0).astype(np.uint8)
            Image.fromarray(gray).save(directory/f'camera_{a}_depth_preview.png')


class VisionWorker:
    """JSON IPC with an isolated GPU interpreter; no Isaac libraries in worker."""
    def __init__(self, directory, pump=None, role='service'):
        import os
        import subprocess
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[4]
        self.log = (self.directory/'worker.log').open('wb', buffering=0)
        environment = os.environ.copy()
        # Native Kit exports Python/loader paths incompatible with this worker.
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
            environment.pop(key, None)
        self.process = subprocess.Popen(['bash', str(root/'scripts/connector_vision_python.sh'),
                            '--role', role, '--output', str(self.directory)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                            env=environment, bufsize=0)
        self.buffer = b''
        self.pump=pump
        self.receive()

    def receive(self):
        import os
        import select
        import time
        deadline = time.monotonic()+300
        last_pump=0.
        while time.monotonic()<deadline:
            if self.pump is not None and time.monotonic()-last_pump>.15:
                self.pump()
                last_pump=time.monotonic()
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    result = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self.log.write(line+b'\n')
                    continue
                if isinstance(result, dict) and 'ok' in result:
                    if not result['ok']:
                        raise RuntimeError(f"RGBD worker: {result.get('error')}; see {self.directory/'worker.log'}")
                    return result
            ready, _, _ = select.select([self.process.stdout], [], [], .2)
            if ready:
                block = os.read(self.process.stdout.fileno(), 65536)
                if not block:
                    raise RuntimeError(f"RGBD worker exited; see {self.directory/'worker.log'}")
                self.buffer += block
        raise TimeoutError('RGBD worker timed out')

    def request(self, request):
        self.process.stdin.write((json.dumps(request)+'\n').encode())
        self.process.stdin.flush()
        return self.receive()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.log):
            stream.close()


def valid_quality(q, registration=False):
    """Measured image support, including dark foreground, not network certainty."""
    return (q['projected_pixels'] >= 80 and q['overlap_pixels'] >= 60
            and q['visible_fraction'] >= .25 and q['dark_fraction'] >= .55
            and q['depth_inlier_fraction'] >= .80
            and q['depth_median_m'] is not None and q['depth_median_m'] <= .002
            and (not registration or q.get('mask_iou', 0) >= .80))


class RGBDPoseSensor:
    """Image-derived pose observation. Its public interface accepts no truth.

The first implementation deliberately runs fixed-step simulation slower than
real time while inference completes. Capture and processing wall times are
logged; this mode makes no real-time hardware claim.
"""
    source = 'Rendered wrist RGBD -> automatic SAM3 masks -> FoundationPose; no object truth'
    noise = 0.
    delay = 0.

    def __init__(self, rig, tool, gaps, directory, hz=15, visualize=False, live=False, pump=None, task_goal=None):
        self.rig, self.tool, self.gaps = rig, tool, gaps
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ipc = self.directory/'ipc'
        self.ipc.mkdir(exist_ok=True)
        self.hz = hz
        self.last_t = None
        self.last_seen = np.full(2, -1e6)
        self.poses = None
        self.visible = np.zeros(2, bool)
        self.confidence = np.zeros(2)
        self.speed = np.zeros(2)
        self.stable = np.zeros(2)
        self.invalid_count = 0
        self.last_detection = -1e6
        self.last_saved = -1e6
        self.samples = []
        self.last_tools = np.array([tool(a) for a in range(2)])
        self.display=None
        if visualize:
            from sim_bridge.connector_vision_display import PerceptionDisplay
            self.display=PerceptionDisplay(self.directory/'visualization',live=live,task_goal=task_goal)
            self.display.update(rig.frames,np.tile(np.eye(4),(2,1,1)),[False,False],[0,1])
        self.events = (self.directory/'inference.jsonl').open('w', buffering=1)
        self.worker = VisionWorker(self.directory,pump=pump if live else None)
        try:
            self.bootstrap(rig.frames)
        except BaseException:
            self.close()
            raise
        rig.consumer = self.update

    def inputs(self, frames):
        paths = []
        for a, frame in enumerate(frames):
            path = self.ipc/f'camera_{a}.npz'
            np.savez(path, **frame)
            paths.append(str(path))
        return paths

    def bootstrap(self, frames):
        import time
        started = time.monotonic()
        if frames is None:
            raise RuntimeError('Cannot localize without rendered RGBD')
        from itertools import product
        paths = self.inputs(frames)
        self.last_detection = float(frames[0]['time'])
        self.events.write(json.dumps(dict(event='redetection', time=self.last_detection))+'\n')
        print(f'RGBD_DETECT time={self.last_detection:.3f}', flush=True)
        if self.display:self.display.set_status('SAM3: detecting connector instances from RGB...')
        mask_paths = [str(self.ipc/f'masks_{a}.npz') for a in range(2)]
        segmented = self.worker.request(dict(op='segment', frames=[dict(input=p, output=m)
                                       for p,m in zip(paths, mask_paths)]))
        if self.display:
            self.display.segment(frames,mask_paths)
            self.display.set_status('FoundationPose: registering models to RGBD...')
            self.display.update(frames,np.tile(np.eye(4),(2,1,1)),[False,False],[0,1],save=True)
        candidates = [[], []]
        for camera, mask_path in enumerate(mask_paths):
            with np.load(mask_path, allow_pickle=False) as z:
                masks, scores = z['masks'], z['scores']
            for index in np.argsort(-scores)[:4]:
                mask = masks[index]
                if np.count_nonzero(mask)<100:
                    continue
                path = self.ipc/f'mask_{camera}_{index}.npy'
                np.save(path, mask)
                for obj in range(2):
                    result = self.worker.request(dict(input=paths[camera], mask=str(path), object=obj,
                        camera=camera, candidate=int(index), reset=True))
                    self.events.write(json.dumps(dict(event='registration', time=self.last_detection, **result))+'\n')
                    if valid_quality(result['quality'], registration=True):
                        pose = frames[camera]['T_world_camera']@result['T_camera_object']
                        q = result['quality']
                        score = q['mask_iou']+q['depth_inlier_fraction']-q['depth_median_m']*100
                        candidates[obj].append((score, pose, result))
        pairs = [(l,r) for l,r in product(*candidates)
                 if np.linalg.norm(l[1][:3,3]-r[1][:3,3])>.025]
        if not pairs:
            raise ValueError('Both connector identities could not be localized from images')
        chosen = max(pairs, key=lambda p:p[0][0]+p[1][0])
        results = []
        for obj, (_, pose, result) in enumerate(chosen):
            result['T_world_object'] = pose.tolist()
            results.append(result)
        self.accept(frames, results, time.monotonic()-started)
        print('RGBD_INITIALIZED cameras='+str([r['camera'] for r in results]), flush=True)

    def accept(self, frames, results, elapsed):
        now = float(frames[0]['time'])
        dt = max(1/self.hz, now-self.last_t) if self.last_t is not None else 1/self.hz
        if self.poses is None:
            self.poses = np.array([r['T_world_object'] for r in results])
        for a, result in enumerate(results):
            q = result['quality']
            self.visible[a] = valid_quality(q)
            if self.visible[a]:
                pose = np.array(result['T_world_object'])
                self.speed[a] = np.linalg.norm(pose[:3,3]-self.poses[a,:3,3])/max(dt, now-self.last_seen[a])
                self.poses[a] = pose
                self.last_seen[a] = now
                self.confidence[a] = np.clip(q['depth_inlier_fraction']*q['dark_fraction'], 0, .99)
                self.stable[a] = self.stable[a]+dt if self.speed[a]<.035 else 0.
            else:
                self.confidence[a] = 0.
                self.stable[a] = 0.
        self.last_t = now
        self.last_tools = np.array([self.tool(a) for a in range(2)])
        self.samples.append(dict(time=now, poses=self.poses.copy(), visible=self.visible.copy(),
                                 confidence=self.confidence.copy(), elapsed=elapsed,
                                 cameras=[r['camera'] for r in results]))
        self.events.write(json.dumps(dict(event='tracking', time=now, elapsed_s=elapsed,
                                         results=results, visible=self.visible.tolist()))+'\n')
        save=now-self.last_saved>=1.
        if self.display:
            rendered=self.ipc/'estimated_mesh_projection.npz'
            self.worker.request(dict(op='visualize',frames=self.inputs(frames),world_poses=self.poses.tolist(),output=str(rendered)))
            self.display.set_status(f'FoundationPose t={now:.3f}s | valid={self.visible.tolist()} | inference={elapsed:.3f}s')
            self.display.update(frames,self.poses,self.visible,[r['camera'] for r in results],rendered,save=save)
        if now-self.last_saved>=1.:
            self.last_saved = now
            self.rig.save(self.directory/f'frames/{len(self.samples):05d}')
            self.overlay(frames, results, self.directory/f'frames/{len(self.samples):05d}')

    def update(self, frames):
        now = float(frames[0]['time'])
        if self.last_t is not None and now-self.last_t < 1/self.hz-1e-6:
            return
        predicted = self.poses.copy()
        gaps = self.gaps()
        for a in range(2):
            if self.visible[a] and .008<gaps[a]<.035:
                predicted[a] = self.tool(a)@np.linalg.inv(self.last_tools[a])@predicted[a]
        paths = self.inputs(frames)
        result = self.worker.request(dict(op='track', frames=paths, predicted_world_poses=predicted.tolist()))
        self.accept(frames, result['objects'], result['elapsed_s'])
        self.invalid_count = 0 if self.visible.all() else self.invalid_count+1
        if self.invalid_count>=3 and now-self.last_detection>1.:
            try:
                self.bootstrap(frames)
                self.invalid_count = 0
            except ValueError as exc:
                self.events.write(json.dumps(dict(event='redetection_failed', time=now, error=str(exc)))+'\n')

    def sample(self, time):
        age = np.maximum(0., time-self.last_seen)
        visible = self.visible & (age<.25)
        return dict(poses=self.poses.copy(), visible=visible, age=age,
                    confidence=np.where(visible,self.confidence,0.), speed=self.speed.copy(),
                    stable=self.stable.copy(), time=time, measurement_time=self.last_t)

    def overlay(self, frames, results, directory):
        from PIL import Image, ImageDraw
        for camera, frame in enumerate(frames):
            image = Image.fromarray(frame['rgb'])
            draw = ImageDraw.Draw(image)
            for a, result in enumerate(results):
                if not self.visible[a]:
                    draw.text((10,10+18*a),f'{a}: LOST',fill='yellow')
                    continue
                pose = np.linalg.inv(frame['T_world_camera'])@result['T_world_object']
                points = np.array([[0,0,0],[.025,0,0],[0,.015,0],[0,0,.015]])@pose[:3,:3].T+pose[:3,3]
                if np.min(points[:,2])<=0:
                    continue
                uv = points@frame['K'].T
                uv = uv[:,:2]/uv[:,2:3]
                for j,color in enumerate(('red','lime','cyan'),1):
                    draw.line([tuple(uv[0]),tuple(uv[j])], fill=color, width=2)
                draw.text(tuple(uv[0]), f'{a}: '+('valid' if self.visible[a] else 'LOST'),fill='yellow')
            image.save(directory/f'camera_{camera}_overlay.png')

    def close(self):
        self.rig.consumer = None
        self.worker.close()
        self.events.close()
        if self.display:self.display.finish()
        if self.samples:
            np.savez_compressed(self.directory/'observations.npz',
                time=np.array([s['time'] for s in self.samples]), poses=np.array([s['poses'] for s in self.samples]),
                visible=np.array([s['visible'] for s in self.samples]), confidence=np.array([s['confidence'] for s in self.samples]),
                processing_wall_s=np.array([s['elapsed'] for s in self.samples]), cameras=np.array([s['cameras'] for s in self.samples]))
        (self.directory/'perception.json').write_text(json.dumps(dict(source=self.source, truth_fallback=False,
            segmentation='SAM3 text prompt; model-fit identity association', hz_simulation=self.hz,
            execution='Synchronous fixed-step feasibility experiment; slower than real time',
            timestamps='Image capture time paired with measured-joint FK',
            simulated_depth='Optical Z with optional synthetic noise/dropout/range; not a calibrated hardware model',
            depth_settings=self.rig.depth_settings), indent=2))
