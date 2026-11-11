"""The extracted geometry must preserve the retained algorithm and its helpers."""
import ast
import importlib.util
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from tasks.connector.components import keying_geometry as extracted

ROOT=Path(__file__).resolve().parents[2]
LEGACY=ROOT/'tasks/connector/legacy/sim_bridge/connector_keying.py'

def reference():
    spec=importlib.util.spec_from_file_location('reference_connector_keying',LEGACY)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_identity_pair_has_complete_executable_geometry_dependencies():
    error=extracted.keyed_error([np.eye(4),np.eye(4)])
    assert len(error['retained_face_area_m2'])==2
    assert error['retained_hooks_caught'] is False

def test_extracted_helpers_are_exact_unmodified_algorithms():
    def functions(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef)}
    original=functions(LEGACY);current=functions(Path(extracted.__file__))
    for name in ('wrap_pi','retained_face_areas'):assert current[name]==original[name]

def test_geometry_and_retention_match_reference_over_contact_pose_variants():
    old=reference();rng=np.random.default_rng(19)
    for roll in (0.,np.pi/2,-np.pi/2):
        for depth in (.0113,.0222,.0228,.0236):
            for _ in range(4):
                female=np.eye(4);female[:3,:3]=Rotation.random(random_state=rng).as_matrix();female[:3,3]=rng.uniform(-.5,.5,3)
                relative=np.eye(4);relative[:3,:3]=Rotation.from_euler('x',roll).as_matrix()@Rotation.from_rotvec(rng.normal(0,.01,3)).as_matrix()
                relative[:3,3]=[.057-depth,*rng.normal(0,.0003,2)]
                pair=[female@relative,female];a=extracted.keyed_error(pair);b=old.keyed_error(pair)
                assert a.keys()==b.keys()
                for key in a:np.testing.assert_allclose(a[key],b[key],atol=1e-14,rtol=1e-12)
                assert extracted.keyed_retained(a)==old.keyed_retained(b)
