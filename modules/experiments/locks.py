"""Content locks for offline evaluations; no mutable controller state."""
from pathlib import Path
import hashlib,json

def local_file(root,reference):
    root=Path(root).resolve();raw=Path(reference);path=(root/raw).absolute() if not raw.is_absolute() else raw.absolute()
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root):raise ValueError('Lock path escapes project')
    current=path
    while current!=root:
        if current.is_symlink():raise ValueError('Lock references cannot use symlinks')
        current=current.parent
    if not path.is_file():raise ValueError('Missing frozen input '+str(reference))
    return path.resolve()

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024*1024):h.update(block)
    return h.hexdigest()

def verify_lock(root,reference,expected=None):
    root=Path(root).resolve();path=local_file(root,reference);actual=digest(path)
    if expected is not None and actual!=expected:raise ValueError('Implementation lock changed after submission')
    value=json.loads(path.read_text())
    if value.get('schema_version')!='expert-evaluation-lock/v1' or not value.get('files'):raise ValueError('Invalid implementation lock')
    for rel,sha in value['files'].items():
        if digest(local_file(root,rel))!=sha:raise ValueError('Frozen implementation/input changed: '+rel)
    return {'path':str(path.relative_to(root)),'sha256':actual,'files':value['files']}
