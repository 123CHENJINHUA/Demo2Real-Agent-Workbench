"""The single explicit source of external environment paths."""
from pathlib import Path
import os
import sys
import json
import subprocess
import tempfile
import yaml

KEYS = ("isaac_sim_root", "isaac_lab_root", "app_python", "data_python", "vision_python", "policy_python", "training_python", "node_executable", "cuda_home", "ffmpeg_executable", "codex_executable")

def load(root):
    return yaml.safe_load((Path(root)/"config/environments.yaml").read_text()) or {}

def resolve(root, key, required=True):
    raw = load(root).get("environments", {}).get(key, "")
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a string")
    raw = raw.replace("${PROJECT_ROOT}", str(Path(root).resolve()))
    if not raw:
        if required:
            raise RuntimeError(f"Environment not configured: {key}")
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{key} requires absolute path or ${{PROJECT_ROOT}}")
    return path

def local_environment(root, extra=None):
    root = Path(root).resolve()
    runtime = root/".runtime"
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    paths = {
        "TMPDIR":"tmp", "XDG_CACHE_HOME":"cache", "XDG_CONFIG_HOME":"config", "XDG_DATA_HOME":"data",
        "PIP_CACHE_DIR":"pip-cache", "npm_config_cache":"npm-cache", "CUDA_CACHE_PATH":"cache/cuda",
        "TORCH_HOME":"cache/torch", "TORCH_EXTENSIONS_DIR":"cache/torch_extensions", "HF_HOME":"cache/huggingface",
        "NUMBA_CACHE_DIR":"cache/numba", "MPLCONFIGDIR":"config/matplotlib", "WARP_CACHE_PATH":"cache/warp",
        "TRITON_CACHE_DIR":"cache/triton", "TORCHINDUCTOR_CACHE_DIR":"cache/torchinductor", "CUPY_CACHE_DIR":"cache/cupy",
        "__GL_SHADER_DISK_CACHE_PATH":"cache/gl", "OMNI_USER_CONFIG_PATH":"config/kit", "OMNI_USER_CACHE_PATH":"cache/kit",
        "OMNI_USER_DATA_PATH":"data/kit", "PLAYWRIGHT_BROWSERS_PATH":"playwright"
    }
    for key, suffix in paths.items():
        p = runtime/suffix
        p.mkdir(parents=True, exist_ok=True)
        env[key] = str(p)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(root)
    env["HF_HUB_OFFLINE"] = "1"
    if extra:
        env.update(extra)
    return env

def validate(root):
    root = Path(root)
    items = []
    for key in KEYS:
        try:
            p = resolve(root, key, False)
            status, detail = "not_configured", "未配置；相关阶段将等待配置"
            if p is not None:
                status, detail = "available", "路径存在（未代表重型模型或硬件已验证）"
                if not p.exists():
                    status, detail = "missing", "路径不存在"
                elif key == "isaac_sim_root" and not (p/"python.sh").is_file():
                    status, detail = "invalid", "缺少 python.sh"
                elif key == "isaac_lab_root" and not (p/"source").is_dir():
                    status, detail = "invalid", "缺少 source 目录"
                elif (key.endswith("python") or key.endswith("executable")) and not (p.is_file() and os.access(p, os.X_OK)):
                    status, detail = "invalid", "需要可执行文件"
            items.append({"key": key, "path": str(p) if p else "", "status": status, "detail": detail})
        except Exception as exc:
            items.append({"key":key, "path":"", "status":"invalid", "detail":str(exc)})
    return {"environments":items, "ok":all(i["status"] == "available" for i in items if i["key"] == "app_python"), "read_only":True}

def save(root, data):
    root = Path(root)
    values = data.get("environments", data)
    if not isinstance(values, dict) or set(values)-set(KEYS):
        raise ValueError("Unknown environment fields")
    current = load(root)
    for key, value in values.items():
        if not isinstance(value, str) or "\x00" in value or "\n" in value:
            raise ValueError(f"Invalid environment path: {key}")
        if value and not (value.startswith("/") or value.startswith("${PROJECT_ROOT}/")):
            raise ValueError("Paths must be absolute or relative to ${PROJECT_ROOT}")
    current.setdefault("environments", {}).update(values)
    current["policy"] = {"external_environments_read_only":True,"project_local_writes":True,"inherit_project_pythonpath":False}
    target = root/"config/environments.yaml"
    stage = target.with_suffix(".yaml.tmp")
    stage.write_text(yaml.safe_dump(current, allow_unicode=True, sort_keys=False))
    stage.replace(target)
    return {"config":current, "validation":validate(root)}
