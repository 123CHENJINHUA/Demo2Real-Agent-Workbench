import numpy as np
import pytest

from modules.scene_building.planar_alignment import apply_line_spacing, compare, fit_reference_camera, line_spacing, observations


TABLE={'position_m':[0.,0.,.7],'size_m':[1.,.5,.1]}
FRAMES={'front.jpg':'videos/front/demo.mp4','side.jpg':'videos/side/demo.mp4'}


def project(eye, focal=.64):
    eye=np.array(eye)
    forward=np.array([0.,0.,.75])-eye
    forward/=np.linalg.norm(forward)
    right=np.cross(forward,[0.,0.,1.])
    right/=np.linalg.norm(right)
    down=np.cross(forward,right)
    rotation=np.stack((right,down,forward))

    def pixel(point):
        x,y=point
        cam=rotation@(np.array([x-.5,(y-.5)*.5,.75])-eye)
        return (.5+cam[:2]/cam[2]*[focal,focal/.75]).tolist()

    return pixel


def test_cross_view_rectification_detects_spacing_error_with_same_order():
    cameras=[project([.15,-1.1,1.5]),project([-1.1,.15,1.45])]
    views=[]
    for ref,camera in zip(FRAMES,cameras):
        views.append({'frame_ref':ref,'corners_uv':[camera(p) for p in ([0,0],[1,0],[1,1],[0,1])],
                      'footprints':[{'object_id':key,'center_uv':camera(xy)}
                                    for key,xy in [('box',[.33,.42]),('tray',[.75,.53])]]})
    measured=observations(views,FRAMES,{'box','tray'})
    assert measured['objects']['box']['table_uv']==pytest.approx([.33,.42])
    fitted=fit_reference_camera(TABLE,views[1]['corners_uv'],(640,480))
    assert fitted['eye_m']==pytest.approx([-1.1,.15,1.45],abs=.001)
    assert fitted['focal_fraction']==pytest.approx(.64)
    settled={'box':{'position_m':[-.17,-.04,.8]},'tray':{'position_m':[.06,.015,.8]}}
    issue=compare(TABLE,measured,settled)
    assert issue['status']=='mismatch' and not issue['spacings'][0]['passed']
    settled['tray']['position_m'][0]=.25
    assert compare(TABLE,measured,settled)['status']=='checked'
    assert observations(views,{'front.jpg':'same','side.jpg':'same'},{'box','tray'})['objects']=={}
    views[1]['footprints'][0]['center_uv']=cameras[1]([.64,.42])
    assert 'box' not in observations(views,FRAMES,{'box','tray'})['objects']


def test_cropped_views_recover_mesh_relative_distance_without_table_corners():
    objects={'tray':{'geometry_type':'mesh','preview_bounds_size_m':[.2,.1,.06],
                     'pose':{'position_m':[-.2,0,.78],'quaternion_wxyz':[1,0,0,0]}},
             'stop':{'pose':{'position_m':[.22,0,.78]}},
             'cargo':{'pose':{'position_m':[.22,0,.85]}}}
    views=[]
    for ref,eye in zip(FRAMES,([.15,-1.1,1.2],[-1.1,.15,1.20])):
        camera=project(eye)
        views.append({'frame_ref':ref,'reference_object':'tray','reference_axis':0,'objects':['tray','stop'],
                      'reference_uv':[camera([.2,.5]),camera([.4,.5])],
                      'parallel_uv':[camera([.1,.7]),camera([.8,.7])],
                      'centers_uv':[camera([.3,.5]),camera([.62,.5])]})
    rows=line_spacing(views,FRAMES,objects,{'cargo':'stop'})
    assert len(rows)==1 and rows[0]['spacing_m']==pytest.approx(.32)
    alignment={'objects':{},'line_spacings':rows}
    assert compare(TABLE,alignment,{key:obj['pose'] for key,obj in objects.items()})['status']=='mismatch'
    assert not list(apply_line_spacing(TABLE,alignment,objects,{'cargo':'stop'},{'stop'}))
    assert {key for key,_ in apply_line_spacing(TABLE,alignment,objects,{'cargo':'stop'},set())}=={'stop','cargo'}
    assert objects['stop']['pose']['position_m']==pytest.approx([.12,0,.78])
    assert objects['cargo']['pose']['position_m']==pytest.approx([.12,0,.85])
    assert compare(TABLE,alignment,{key:obj['pose'] for key,obj in objects.items()})['status']=='checked'
