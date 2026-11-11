"""Isolated image-only GPU inference. No simulator or truth files are imported.

Borrow existing installed dependencies/weights read-only; all writes go to the
connector RGBD output tree. JSON lines provide a persistent process interface.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[4]
VISION = ROOT/'vendor/vision'


class Segmenter:
    def __init__(self):
        sys.path.insert(0, str(VISION/'third_party/sam3'))
        import torch
        # FoundationPose's refiner changes the process-wide default tensor
        # type to CUDA. SAM3 creates and pins CPU prompt tensors internally.
        torch.set_default_tensor_type(torch.FloatTensor)
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
        self.torch = torch
        model = build_sam3_image_model(checkpoint_path=str(VISION/'weights/sam3.pt'),
                                      load_from_HF=False, device='cuda')
        self.processor = Sam3Processor(model, device='cuda', confidence_threshold=.25)

    def process(self, req):
        import numpy as np
        from PIL import Image
        with np.load(req['input'], allow_pickle=False) as data:
            rgb = data['rgb']
        with self.torch.inference_mode(), self.torch.autocast('cuda', dtype=self.torch.bfloat16):
            state = self.processor.set_image(Image.fromarray(rgb))
            result = self.processor.set_text_prompt(prompt=req.get('prompt', 'black electrical connector'), state=state)
            scores = result['scores'].detach().float().cpu().numpy().reshape(-1)
            masks = result['masks'].detach().cpu().numpy().reshape(-1, *rgb.shape[:2]) > .5
        np.savez_compressed(req['output'], masks=masks, scores=scores)
        return dict(count=len(scores), scores=scores.tolist())


class PoseEstimator:
    def __init__(self, output):
        sys.path.insert(0, str(VISION/'third_party/FoundationPose'))
        import torch
        # SAM3 enables TF32 globally. Restore full float32 geometric products.
        torch.set_float32_matmul_precision('highest')
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        includes = list((Path(torch.__file__).resolve().parents[1]/'nvidia').glob('*/include'))
        os.environ['CPATH'] = os.pathsep.join(map(str, includes))+os.pathsep+os.environ.get('CPATH', '')
        major, minor = torch.cuda.get_device_capability()
        os.environ['TORCH_CUDA_ARCH_LIST'] = f'{major}.{minor}'
        import warp
        warp.config.kernel_cache_dir = str(ROOT/'output/connector_rgbd/runtime/warp')
        # Upstream hardcodes a temporary OBJ in /tmp. Load a local in-memory
        # compatibility adaptation so even those exports obey project isolation;
        # the existing FoundationPose source/environment stays unchanged.
        import types
        source_path = VISION/'third_party/FoundationPose/estimater.py'
        source = source_path.read_text()
        needle = "f'/tmp/{uuid.uuid4()}.obj'"
        if source.count(needle)!=1:
            raise RuntimeError('FoundationPose temporary export changed; audit local adaptation')
        source = source.replace(needle, "os.path.join(os.environ['TMPDIR'], str(uuid.uuid4())+'.obj')")
        module = types.ModuleType('connector_foundationpose')
        module.__file__ = str(source_path)
        sys.modules[module.__name__] = module
        exec(compile(source, str(source_path), 'exec'), module.__dict__)
        FoundationPose, ScorePredictor = module.FoundationPose, module.ScorePredictor
        PoseRefinePredictor, dr = module.PoseRefinePredictor, module.dr
        from Utils import nvdiffrast_render
        import trimesh
        scorer, refiner = ScorePredictor(), PoseRefinePredictor()
        glctx = dr.RasterizeCudaContext()
        self.estimators = []
        for a in range(2):
            mesh = trimesh.load(next((ROOT/f'assets/objects/connector/model/{a+1}').glob('*lowpoly.obj')), force='mesh')
            estimator = FoundationPose(model_pts=mesh.vertices, model_normals=mesh.vertex_normals,
                                      mesh=mesh, scorer=scorer, refiner=refiner, glctx=glctx,
                                      debug=0, debug_dir=str(output/f'debug_{a}'))
            self.estimators.append(estimator)
        self.torch = torch
        self.render = nvdiffrast_render

    def process(self, req):
        import numpy as np
        with np.load(req['input'], allow_pickle=False) as data:
            rgb, depth, K = data['rgb'].copy(), data['depth_m'].copy(), data['K'].copy()
        estimator = self.estimators[req['object']]
        if req.get('reset', True):
            mask = np.load(req['mask'], allow_pickle=False).astype(bool)
            pose = estimator.register(K=K, rgb=rgb, depth=depth, ob_mask=mask, iteration=req.get('iterations', 5))
        else:
            # Last pose is expressed in this frame's camera, predicted using
            # timestamped FK and the last IMAGE estimate, never object truth.
            if 'initial_camera_pose' in req:
                estimator.pose_last = self.torch.tensor(req['initial_camera_pose'], dtype=self.torch.float32, device='cuda')@self.torch.linalg.inv(estimator.get_tf_to_centered_mesh())
            pose = estimator.track_one(rgb=rgb, depth=depth, K=K, iteration=req.get('iterations', 2))
        pose = np.asarray(pose, dtype=float)
        if pose.shape != (4, 4) or not np.isfinite(pose).all():
            raise ValueError('Nonfinite pose estimate')
        u, _, vh = np.linalg.svd(pose[:3,:3])
        pose[:3,:3] = u@np.diag([1.,1.,np.linalg.det(u@vh)])@vh
        refinement=None
        if req.get('depth_refine',True):
            pose,refinement=self.refine_depth(req['object'],pose,[dict(rgb=rgb,depth_m=depth,K=K,T_world_camera=np.eye(4))])
        estimator.pose_last = self.torch.tensor(pose, dtype=self.torch.float32, device='cuda')@self.torch.linalg.inv(estimator.get_tf_to_centered_mesh())
        self.torch.cuda.synchronize()
        mask = np.load(req['mask'], allow_pickle=False).astype(bool) if req.get('mask') else None
        quality, rendered_depth = self.quality(req['object'], pose, rgb, depth, K, mask)
        if req.get('render_output'):
            np.savez_compressed(req['render_output'], depth_m=rendered_depth, silhouette=rendered_depth>0)
        return dict(T_camera_object=pose.tolist(), quality=quality,depth_refinement=refinement,
                    object=req['object'], camera=req.get('camera'), candidate=req.get('candidate'))

    def refine_depth(self,obj,world_pose,frames):
        """Projective two-view ICP on rigid rear body surfaces, never the clips.

        The observations are optical-Z depth and RGB. Camera transforms come
        from calibration and capture-time FK. No simulator geometry is read.
        """
        import cv2
        import numpy as np
        from sim_bridge.connector_depth_refinement import increment
        pose=np.array(world_pose,dtype=float).copy();estimator=self.estimators[obj]
        start=pose.copy();counts=[]
        for _ in range(6):
            points=[];normals=[];observed=[]
            for frame in frames:
                depth,K,rgb=frame['depth_m'],frame['K'],frame['rgb']
                camera_pose=np.linalg.inv(frame['T_world_camera'])@pose
                centered=self.torch.tensor(camera_pose,dtype=self.torch.float32,device='cuda')@self.torch.linalg.inv(estimator.get_tf_to_centered_mesh())
                _,rendered,normal=self.render(K=K,H=depth.shape[0],W=depth.shape[1],ob_in_cams=centered[None],
                    glctx=estimator.glctx,mesh_tensors=estimator.mesh_tensors,get_normal=True)
                rendered=rendered.detach().cpu().numpy().reshape(depth.shape)
                normal=normal.detach().cpu().numpy().reshape(*depth.shape,3)
                mask=cv2.erode((rendered>0).astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
                mask &= (depth>0)&(abs(depth-rendered)<.002)&(rgb.mean(axis=-1)<120)&(abs(normal[:,:,2])>.15)
                y,x=np.nonzero(mask)
                if len(x)<80:continue
                stride=max(1,len(x)//1500);x,y=x[::stride],y[::stride]
                rays=np.column_stack(((x-K[0,2])/K[0,0],(y-K[1,2])/K[1,1],np.ones(len(x))))
                p=(rays*rendered[y,x,None]-camera_pose[:3,3])@camera_pose[:3,:3]
                q=(rays*depth[y,x,None]-camera_pose[:3,3])@camera_pose[:3,:3]
                n=normal[y,x]@camera_pose[:3,:3]
                # Exclude moving latch faces and socket entry / mating overlap.
                rigid=(p[:,0]>-.004) if obj==0 else (p[:,0]<.013)
                points.append(p[rigid]);observed.append(q[rigid]);normals.append(n[rigid])
            count=sum(len(p) for p in points);counts.append(count)
            if count<100:break
            delta=increment(np.concatenate(points),np.concatenate(normals),np.concatenate(observed))
            pose=pose@delta
            if np.linalg.norm(delta[:3,3])<.000002 and np.linalg.norm(delta[:3,:3]-np.eye(3))<.0001:break
        from scipy.spatial.transform import Rotation
        return pose,dict(points=counts,translation_update_m=float(np.linalg.norm(pose[:3,3]-start[:3,3])),
            rotation_update_rad=float(Rotation.from_matrix(start[:3,:3].T@pose[:3,:3]).magnitude()),
            source='RGBD projective point-to-plane ICP on rigid CAD body surfaces')

    def quality(self, obj, pose, rgb, depth, K, mask=None):
        import numpy as np
        estimator = self.estimators[obj]
        h, w = depth.shape
        centered = self.torch.tensor(pose, dtype=self.torch.float32, device='cuda')@self.torch.linalg.inv(estimator.get_tf_to_centered_mesh())
        _, rendered_depth, _ = self.render(K=K, H=h, W=w, ob_in_cams=centered.reshape(1,4,4),
                                          glctx=estimator.glctx, mesh_tensors=estimator.mesh_tensors)
        rendered_depth = rendered_depth.detach().float().cpu().numpy().reshape(h, w)
        silhouette = rendered_depth > 0
        valid = silhouette & (depth > 0)
        # Pixels occluded by a nearer measured surface do not validate a pose.
        unoccluded = valid & (depth >= rendered_depth-.003)
        residual = np.abs(depth[unoccluded]-rendered_depth[unoccluded])
        quality = dict(projected_pixels=int(silhouette.sum()), overlap_pixels=int(unoccluded.sum()),
                       visible_fraction=float(unoccluded.sum()/max(1,silhouette.sum())),
                       depth_median_m=float(np.median(residual)) if len(residual) else None,
                       depth_p90_m=float(np.percentile(residual,90)) if len(residual) else None,
                       depth_inlier_fraction=float(np.mean(residual<.003)) if len(residual) else 0.,
                       dark_fraction=float(np.mean(rgb[unoccluded].mean(axis=-1)<120)) if unoccluded.any() else 0.)
        if mask is not None:
            quality['mask_iou'] = float(np.count_nonzero(silhouette & mask)/max(1,np.count_nonzero(silhouette | mask)))
        return quality, rendered_depth

    def render_views(self, req):
        import numpy as np
        layers=[]
        for path in req['frames']:
            with np.load(path,allow_pickle=False) as z:
                rgb,depth,K,T=z['rgb'],z['depth_m'],z['K'],z['T_world_camera']
            layers.append([self.quality(a,np.linalg.inv(T)@pose,rgb,depth,K)[1]
                           for a,pose in enumerate(req['world_poses'])])
        np.savez_compressed(req['output'],rendered_depth_m=np.asarray(layers,dtype=np.float32))
        return dict(render_output=req['output'],source='Known meshes at image-estimated poses; visualization only')


class MeshRenderer:
    """Offline rendering of recorded FoundationPose outputs, without re-inference."""
    def __init__(self):
        sys.path.insert(0,str(VISION/'third_party/FoundationPose'))
        import torch
        includes=list((Path(torch.__file__).resolve().parents[1]/'nvidia').glob('*/include'))
        os.environ['CPATH']=os.pathsep.join(map(str,includes))+os.pathsep+os.environ.get('CPATH','')
        major,minor=torch.cuda.get_device_capability()
        os.environ['TORCH_CUDA_ARCH_LIST']=f'{major}.{minor}'
        torch.set_float32_matmul_precision('highest')
        import warp
        warp.config.kernel_cache_dir=str(ROOT/'output/connector_rgbd/runtime/warp')
        from Utils import make_mesh_tensors,nvdiffrast_render,dr
        import trimesh
        self.meshes=[make_mesh_tensors(trimesh.load(next((ROOT/f'assets/objects/connector/model/{a+1}').glob('*lowpoly.obj')),force='mesh')) for a in range(2)]
        self.render=nvdiffrast_render
        self.glctx=dr.RasterizeCudaContext()
        self.torch=torch

    def process(self,req):
        import numpy as np
        layers=[]
        for path in req['frames']:
            with np.load(path,allow_pickle=False) as z:K,T,shape=z['K'],z['T_world_camera'],z['depth_m'].shape
            view=[]
            for a,pose in enumerate(req['world_poses']):
                camera_pose=self.torch.tensor(np.linalg.inv(T)@pose,device='cuda',dtype=self.torch.float32)[None]
                _,depth,_=self.render(K=K,H=shape[0],W=shape[1],ob_in_cams=camera_pose,glctx=self.glctx,mesh_tensors=self.meshes[a])
                view.append(depth.detach().float().cpu().numpy().reshape(shape))
            layers.append(view)
        np.savez_compressed(req['output'],rendered_depth_m=np.asarray(layers,dtype=np.float32))
        return dict(render_output=req['output'],source='Recorded FoundationPose outputs; visualization only')


class Service:
    def __init__(self, output):
        self.output = output
        self.pose = None

    def process(self, req):
        import numpy as np
        if req.get('op') == 'segment':
            # Segmentation and pose networks share the GPU in sequence. Do not
            # keep the large text segmentation network resident during control.
            import gc
            self.pose = None
            gc.collect()
            import torch
            torch.cuda.empty_cache()
            segmenter = Segmenter()
            results = [segmenter.process(item) for item in req['frames']]
            del segmenter
            gc.collect()
            torch.cuda.empty_cache()
            return dict(frames=results)
        if self.pose is None:
            self.pose = PoseEstimator(self.output)
        if req.get('op')=='visualize':
            return self.pose.render_views(req)
        if req.get('op') not in ('track','refine'):
            return self.pose.process(req)
        frames = []
        for path in req['frames']:
            with np.load(path, allow_pickle=False) as z:
                frames.append({key:z[key].copy() for key in ('rgb','depth_m','K','T_world_camera')})
        results = []
        if req.get('op')=='refine':
            for obj,pose in enumerate(req['predicted_world_poses']):
                refined,info=self.pose.refine_depth(obj,pose,frames)
                results.append(dict(T_world_object=refined.tolist(),depth_refinement=info))
            return dict(objects=results)
        for obj, world_pose in enumerate(req['predicted_world_poses']):
            choices = []
            for camera, frame in enumerate(frames):
                pose = np.linalg.inv(frame['T_world_camera'])@world_pose
                q, _ = self.pose.quality(obj, pose, frame['rgb'], frame['depth_m'], frame['K'])
                score = q['depth_inlier_fraction']*q['visible_fraction']*q['dark_fraction']*np.sqrt(q['projected_pixels'])
                choices.append((score, camera, pose))
            _, camera, pose = max(choices, key=lambda v:v[0])
            result = self.pose.process(dict(input=req['frames'][camera], object=obj, reset=False,
                                           initial_camera_pose=pose.tolist(), camera=camera, iterations=2,depth_refine=False))
            world_estimate=frames[camera]['T_world_camera']@result['T_camera_object']
            refined,info=self.pose.refine_depth(obj,world_estimate,frames)
            result['T_world_object']=refined.tolist();result['depth_refinement']=info
            result['T_camera_object']=(np.linalg.inv(frames[camera]['T_world_camera'])@refined).tolist()
            result['quality']=self.pose.quality(obj,np.asarray(result['T_camera_object']),
                frames[camera]['rgb'],frames[camera]['depth_m'],frames[camera]['K'])[0]
            result['view_scores'] = [float(c[0]) for c in choices]
            results.append(result)
        return dict(objects=results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['segment', 'pose', 'service', 'render'], required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--request', help='One-shot JSON request, otherwise JSON lines over stdin')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=True)
    control = sys.stdout
    def reply(value):
        print(json.dumps(value, allow_nan=False), file=control, flush=True)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            model = {'segment':Segmenter,'render':MeshRenderer}.get(args.role)
            model = model() if model else (Service(output) if args.role=='service' else PoseEstimator(output))
            reply(dict(ok=True, ready=True))
            requests = [Path(args.request).read_text()] if args.request else sys.stdin
            for line in requests:
                start = time.monotonic()
                try:
                    result = model.process(json.loads(line))
                    reply(dict(ok=True, elapsed_s=time.monotonic()-start, **result))
                except Exception as exc:
                    traceback.print_exc()
                    reply(dict(ok=False, error=f'{type(exc).__name__}: {exc}'))
        except Exception as exc:
            traceback.print_exc()
            reply(dict(ok=False, fatal=True, error=f'{type(exc).__name__}: {exc}'))
            raise


if __name__ == '__main__':
    main()
