"""Image-only perception panels. No simulator object poses or semantic labels.

SAM snapshots keep their original RGB and timestamp. FoundationPose surfaces
are projected known meshes at the IMAGE-estimated poses, with occlusion marked.
"""
from pathlib import Path
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

COLORS = [(32, 220, 255), (255, 166, 40), (190, 100, 255), (255, 70, 130)]


def font(size=16):
    return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)


def boundary(mask):
    image=Image.fromarray(np.uint8(mask)*255)
    return np.asarray(image.filter(ImageFilter.MaxFilter(3))) != np.asarray(image.filter(ImageFilter.MinFilter(3)))


def segmentation_image(rgb, masks, scores):
    array=np.asarray(rgb).copy()
    for i,mask in enumerate(masks):
        color=np.array(COLORS[i%len(COLORS)])
        array[mask]=(array[mask]*.60+color*.40).astype(np.uint8)
        array[boundary(mask)]=color
    result=Image.fromarray(array)
    draw=ImageDraw.Draw(result)
    for i,mask in enumerate(masks):
        y,x=np.where(mask)
        if not len(x):continue
        color=COLORS[i%len(COLORS)]
        draw.rectangle((int(x.min()),int(y.min()),int(x.max()),int(y.max())),outline=color,width=2)
        draw.text((int(x.min()),max(0,int(y.min())-20)),f'candidate {i}  SAM={scores[i]:.2f}',font=font(),fill=color,stroke_width=1,stroke_fill='black')
    if not len(masks):draw.text((12,12),'NO SEGMENTATION DETECTIONS',font=font(18),fill='yellow',stroke_width=1,stroke_fill='black')
    return result


def depth_image(depth):
    valid=np.isfinite(depth)&(depth>0)
    low,high=np.percentile(depth[valid],[3,97]) if valid.any() else (0.,1.)
    gray=np.where(valid,255*(1-np.clip((depth-low)/max(.01,high-low),0,1)),0).astype(np.uint8)
    return Image.fromarray(gray).convert('RGB')


def pose_image(frame, rendered, poses, visible, cameras, camera):
    array=frame['rgb'].copy()
    measurements=frame['depth_m']
    for a,depth in enumerate(rendered):
        if not visible[a]:continue
        silhouette=depth>0
        observed=silhouette&(measurements>0)&(measurements>=depth-.003)
        array[observed]=(array[observed]*.72+np.array(COLORS[a])*.28).astype(np.uint8)
        edge=boundary(silhouette)
        array[edge&observed]=COLORS[a]
    image=Image.fromarray(array)
    draw=ImageDraw.Draw(image)
    for a,world_pose in enumerate(poses):
        q='TRACKED' if visible[a] else 'LOST - no trusted pose'
        view='inference' if cameras[a]==camera else f'reprojected from C{cameras[a]}'
        draw.text((8,8+a*22),f'{a}: {q} | {view}',font=font(),fill=COLORS[a],stroke_width=1,stroke_fill='black')
        if not visible[a]:continue
        pose=np.linalg.inv(frame['T_world_camera'])@world_pose
        points=np.array([[0,0,0],[.025,0,0],[0,.015,0],[0,0,.015]])@pose[:3,:3].T+pose[:3,3]
        if points[:,2].min()<=.001:continue
        uv=points@frame['K'].T;uv=uv[:,:2]/uv[:,2:3]
        for j,color in enumerate(('red','lime','cyan'),1):
            draw.line([tuple(uv[0]),tuple(uv[j])],fill=color,width=3)
    return image


class PerceptionDisplay:
    def __init__(self, directory, live=False, mode='LIVE', task_goal=None):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.segmentation=None
        self.provider=None
        self.window=None
        self.canvas=None
        self.frozen=False
        self.status='Waiting for RGBD / SAM3'
        self.mode=mode
        self.goal={'grasp':'GRASP ONLY (no insertion)', 'insert':'GRASP + INSERT',
                   'place':'FULL: grasp + insert + place',
                   'release':'FULL: grasp + keyed insert + direct jaw release'}.get(task_goal,'')
        if live:
            import omni.ui as ui
            self.provider=ui.ByteImageProvider()
            self.window=ui.Window('Connector vision | SAM3 + FoundationPose',width=1260,height=690)
            with self.window.frame:
                with ui.VStack(spacing=5):
                    self.label=ui.Label(self.status,height=24)
                    with ui.HStack(height=26):
                        ui.Button('Save current panels',clicked_fn=self.snapshot)
                        ui.Button('Freeze / resume DISPLAY',clicked_fn=self.toggle_freeze)
                    ui.ImageWithProvider(self.provider,fill_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT)

    def set_status(self,text):
        self.status=text
        if self.window is not None:self.label.text=(self.goal+' | ' if self.goal else '')+text

    def toggle_freeze(self):
        self.frozen=not self.frozen

    def snapshot(self):
        if self.canvas is not None:
            import time
            self.canvas.save(self.directory/f'manual_{time.time_ns()}.png')

    def segment(self,frames,paths):
        self.segmentation=[]
        stamp=float(frames[0]['time'])
        target=self.directory/f'segmentation_{stamp:.3f}'
        target.mkdir(exist_ok=True)
        for camera,(frame,path) in enumerate(zip(frames,paths)):
            with np.load(path,allow_pickle=False) as z:masks,scores=z['masks'].copy(),z['scores'].copy()
            image=segmentation_image(frame['rgb'],masks,scores)
            self.segmentation.append((image,stamp))
            Image.fromarray(frame['rgb']).save(target/f'camera_{camera}_rgb.png')
            image.save(target/f'camera_{camera}_sam3.png')
            np.savez_compressed(target/f'camera_{camera}_masks.npz',masks=masks,scores=scores,time=stamp)
        (target/'source.json').write_text(json.dumps(dict(source='Actual SAM3 inference, not simulator segmentation',capture_time=stamp,
            candidates_are_not_object_identities=True),indent=2))

    def update(self,frames,poses,visible,cameras,render_path=None,save=False):
        if self.frozen:return
        now=float(frames[0]['time'])
        rendered=None
        if render_path is not None:
            with np.load(render_path,allow_pickle=False) as z:rendered=z['rendered_depth_m']
        canvas=Image.new('RGB',(1280,594),'#20242b')
        draw=ImageDraw.Draw(canvas)
        draw.text((10,7),f'{self.mode} | t={now:.3f}s | FP cyan=male / orange=female | SAM=candidates | X/Y/Z=red/green/cyan',font=font(16),fill='white')
        for camera,frame in enumerate(frames):
            y=35+camera*278
            draw.text((8,y),f'C{camera} wrist | segmentation retains its OWN capture frame; FP refreshes during tracking',font=font(14),fill='white')
            seg=self.segmentation[camera] if self.segmentation else None
            seg_image=seg[0] if seg else Image.new('RGB',(640,480),'#30343b')
            fp=pose_image(frame,rendered[camera],poses,visible,cameras,camera) if rendered is not None else Image.fromarray(frame['rgb'])
            columns=[('RGB',Image.fromarray(frame['rgb'])),
                     (f'SAM3 snapshot t={seg[1]:.3f}s' if seg else 'SAM3: initializing',seg_image),
                     ('FoundationPose: estimated mesh + axes',fp),('Depth (display only)',depth_image(frame['depth_m']))]
            for col,(title,image) in enumerate(columns):
                x=320*col
                draw.text((x+7,y+21),title,font=font(12),fill='#ddddee')
                canvas.paste(image.resize((320,240),Image.Resampling.BILINEAR),(x,y+39))
        self.canvas=canvas
        temporary=self.directory/'.latest.png.tmp'
        canvas.save(temporary,format='PNG')
        temporary.replace(self.directory/'latest.png')
        if save:
            target=self.directory/f'frame_{now:.3f}'
            target.mkdir(exist_ok=True)
            canvas.save(target/'panels.png')
            if rendered is not None:
                for camera,frame in enumerate(frames):
                    pose_image(frame,rendered[camera],poses,visible,cameras,camera).save(target/f'camera_{camera}_foundationpose.png')
        if self.provider is not None:
            rgba=np.asarray(canvas.convert('RGBA'))
            self.provider.set_bytes_data(rgba.ravel().tolist(),[canvas.width,canvas.height])

    def finish(self):
        self.set_status('Run ended | panel retains final image; see task completion status')
