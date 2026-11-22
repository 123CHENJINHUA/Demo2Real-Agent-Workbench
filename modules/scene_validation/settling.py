"""Read the sampled motion that accompanies a failed local settling check."""

import numpy as np
from scipy.spatial.transform import Rotation


def settling_evidence(manifest, checks):
    failed={row['id'].split(':',1)[1] for row in checks if row['id'].startswith('settled:') and not row['passed']}
    samples=manifest.get('settling_samples') or []
    evidence=[]
    roles=(manifest.get('scene_approximation') or {}).get('object_roles',{})
    for obj in manifest.get('objects',[]):
        key=obj['id']
        if key not in failed:
            continue
        support=obj.get('preview_support_id') or roles.get(key,{}).get('support_id')
        poses=[]
        for sample in samples:
            body=sample['objects'].get(key)
            if body:
                poses.append({'time_s':sample['time_s'],'object_world':body,
                              'support_world':sample['objects'].get(support)})
        if not poses:
            continue

        def transforms(name):
            positions=np.asarray([row[name]['position_m'] for row in poses])
            rotations=Rotation.from_quat(np.asarray([row[name]['quaternion_wxyz'] for row in poses])[:,[1,2,3,0]])
            return positions,rotations

        positions,rotations=transforms('object_world')
        review={'object_id':key,'label':obj.get('label'),'declared_support_id':support,'samples':poses,
                'settling_duration_s':manifest.get('settling_duration_s')}
        if all(row['support_world'] is not None for row in poses):
            reference,axes=transforms('support_world')
            positions=axes.inv().apply(positions-reference)
            rotations=axes.inv()*rotations
            review['reference_frame']='declared_support'
            review['maximum_support_translation_m']=float(np.linalg.norm(reference-reference[0],axis=1).max())
        else:
            review['reference_frame']='world'
        review['maximum_translation_from_first_sample_m']=float(np.linalg.norm(positions-positions[0],axis=1).max())
        review['maximum_rotation_from_first_sample_deg']=float(np.degrees((rotations[0].inv()*rotations).magnitude().max()))
        evidence.append(review)
    return evidence
