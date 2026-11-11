"""Safe, independent demonstration ingestion. No decoder or ML runtime required."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import zipfile
import uuid
from datetime import datetime, timezone

VERSION = "demo_import.v1"
VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm"}


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Output must remain inside project root")
    return resolved


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _hash(path: Path, cancel=lambda: None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            cancel()
            digest.update(chunk)
    return digest.hexdigest()


def _no_symlink_path(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlink input rejected: {current}")


def _open_input(path: Path) -> int:
    """Open every ancestor without following links, including rename-race checks."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    current = os.open(path.anchor, directory_flags)
    try:
        for part in path.parts[1:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = next_directory
        return os.open(path.name, flags, dir_fd=current)
    finally:
        os.close(current)


def inspect_dataset(dataset: Path, entries: list[dict]) -> dict:
    """Inspect metadata and NPY headers without pickle or heavy array imports."""
    result = {"format": "folder", "metadata_files": [], "videos": [], "arrays": [], "warnings": []}
    for entry in entries:
        relative = entry["path"]
        path = dataset / relative
        if path.suffix.lower() in VIDEO_SUFFIXES:
            result["videos"].append({"path": relative, "bytes": entry["bytes"], "decoded": False})
        if path.name in {"info.json", "episodes.jsonl", "tasks.jsonl", "conversion.json", "nero_depth_encoding.json"}:
            record = {"path": relative}
            if path.suffix == ".json" and path.stat().st_size <= 2_000_000:
                try:
                    value = json.loads(path.read_text())
                    record["content"] = value
                    if path.name == "nero_depth_encoding.json":
                        result["depth_encoding"] = value
                    if path.name == "info.json" and isinstance(value, dict):
                        result["format"] = "lerobot"
                        for key in ("fps", "total_frames", "total_episodes", "robot_type", "features", "video_path", "data_path", "codebase_version"):
                            if key in value:
                                result[key] = value[key]
                except (ValueError, OSError) as exc:
                    record["error"] = str(exc)
            result["metadata_files"].append(record)
        if path.suffix.lower() == ".npz":
            archive = {"path": relative, "arrays": [], "data_loaded": False}
            try:
                with zipfile.ZipFile(path) as z:
                    for item in z.infolist()[:256]:
                        if not item.filename.endswith(".npy"):
                            continue
                        if item.file_size > 8 * 1024**3:
                            archive.setdefault("warnings", []).append("Array exceeds inspection budget")
                            continue
                        with z.open(item) as handle:
                            prefix = handle.read(8)
                            if prefix[:6] != b"\x93NUMPY":
                                continue
                            length_size = 2 if prefix[6] == 1 else 4
                            raw_length = handle.read(length_size)
                            size = int.from_bytes(raw_length, "little")
                            if size > 65536:
                                raise ValueError("NPY header exceeds inspection budget")
                            import ast
                            header = ast.literal_eval(handle.read(size).decode("latin1"))
                            archive["arrays"].append({"name": item.filename[:-4], "shape": list(header["shape"]), "dtype": str(header["descr"]), "fortran_order": header["fortran_order"], "compressed_bytes": item.compress_size})
                if result["format"] == "folder":
                    result["format"] = "npz"
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, SyntaxError) as exc:
                archive["error"] = str(exc)
            result["arrays"].append(archive)
    # Optional local app dependency; no external environment guessing or pickle.
    episode_files = [entry["path"] for entry in entries if entry["path"].startswith("meta/episodes/") and entry["path"].endswith(".parquet")]
    if episode_files:
        result["episode_metadata_status"] = "needs_pyarrow_dependency"
        try:
            import pyarrow.parquet as pq
            episodes = []
            for reference in episode_files[:64]:
                parquet = pq.ParquetFile(dataset / reference)
                if parquet.metadata.num_rows > 10000:
                    raise ValueError("Episode metadata row budget exceeded")
                columns = [name for name in parquet.schema_arrow.names if not name.startswith("stats/")]
                episodes.extend(parquet.read(columns=columns).to_pylist())
                if len(episodes) > 10000:
                    raise ValueError("Episode metadata row budget exceeded")
            result["episodes"] = episodes
            result["episode_metadata_status"] = "read"
        except ImportError:
            pass
        except (ValueError, OSError) as exc:
            result["episode_metadata_status"] = "failed"
            result["episode_metadata_error"] = str(exc)
    for record in result["videos"]:
        record["stream"] = next((part for part in Path(record["path"]).parts if part.startswith("observation.images.")), None)
        feature = result.get("features", {}).get(record["stream"], {})
        record["is_depth"] = feature.get("info", {}).get("is_depth_map") is True or "depth" in (record["stream"] or record["path"]).casefold()
    result["warnings"].append("Video/depth quality and temporal alignment require decoder review; files alone do not establish metric RGBD or task semantics.")
    depth = result.get("depth_encoding", {})
    encoding = json.dumps(depth).casefold()
    result["depth_status"] = "lossy_encoded_not_metric_verified" if any(token in encoding for token in ("lossy", "8-bit", "h.264")) else "unverified"
    return result


def run(ctx: dict) -> dict:
    root = Path(ctx["root"]).resolve()
    output = _inside(root, Path(ctx["output_dir"]))
    output.mkdir(parents=True, exist_ok=True)
    config = ctx.get("input", {})
    raw = config.get("source_path") or config.get("demo_path")
    if not raw:
        raise ValueError("source_path is required")
    source = Path(raw).expanduser().absolute()
    _no_symlink_path(source)
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Demonstration source must be a directory")
    if root == source or root.is_relative_to(source) or output.is_relative_to(source):
        raise ValueError("Source must not contain the project or import destination")
    emit = ctx.get("emit", lambda *args: None)
    cancel = ctx.get("check_cancel", lambda: None)
    max_files = int(config.get("max_files", 100000))
    max_bytes = int(config.get("max_total_bytes", 50 * 1024**3))
    if max_files < 1 or max_bytes < 1:
        raise ValueError("Import budgets must be positive")
    entries = []
    total = 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        cancel()
        for name in list(dirs) + names:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Symlink input rejected: {path.relative_to(source)}")
            if name in dirs:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Non-regular input rejected: {path.relative_to(source)}")
            total += info.st_size
            if len(entries) + 1 > max_files or total > max_bytes:
                raise ValueError("Import exceeds file or byte budget")
            entries.append({"path": path.relative_to(source).as_posix(), "bytes": info.st_size, "source_mtime_ns": info.st_mtime_ns, "source_inode": info.st_ino})
    if not entries:
        raise ValueError("Demonstration folder is empty")
    entries.sort(key=lambda x: x["path"])
    manifest_path = output / "manifest.json"
    target = output / "dataset"
    if target.exists():
        if not manifest_path.is_file():
            raise ValueError("Existing dataset has no manifest; choose a fresh output")
        previous = json.loads(manifest_path.read_text())
        if previous.get("source_path") != str(source) or [(e["path"], e["bytes"], e.get("source_mtime_ns")) for e in previous["files"]] != [(e["path"], e["bytes"], e["source_mtime_ns"]) for e in entries]:
            raise ValueError("Source changed; create a new run instead of replacing a completed import")
        for item in previous["files"]:
            cancel()
            candidate = target / item["path"]
            if candidate.is_symlink() or not candidate.is_file() or _hash(candidate, cancel) != item["sha256"]:
                raise ValueError("Imported dataset integrity check failed")
        return previous["result"] | {"reused_verified_import": True}
    staging = _inside(root, output / ".importing")
    if staging.is_symlink():
        raise ValueError("Staging symlink rejected")
    staging.mkdir(exist_ok=True)
    checkpoint_path = output / "import_checkpoint.json"
    completed = []
    copied = 0
    emit("progress", {"unit": "files", "completed": 0, "total": len(entries), "total_bytes": total})
    for index, item in enumerate(entries):
        cancel()
        original = source / item["path"]
        destination = staging / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        _no_symlink_path(destination.parent)
        if not destination.parent.resolve().is_relative_to(staging):
            raise ValueError("Staging path escaped output")
        fd = _open_input(original)
        try:
            before = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (item["source_inode"], item["bytes"], item["source_mtime_ns"]):
                raise ValueError("Source changed during import")
            digest = hashlib.sha256()
            partial = destination.with_name(destination.name + ".copying")
            if partial.is_symlink():
                raise ValueError("Staging partial symlink rejected")
            partial_fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "rb", closefd=False) as src, os.fdopen(partial_fd, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    cancel()
                    digest.update(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            after = os.fstat(fd)
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise ValueError("Source changed during import")
            os.replace(partial, destination)
        finally:
            os.close(fd)
        item = {k: v for k, v in item.items() if k != "source_inode"} | {"sha256": digest.hexdigest()}
        completed.append(item)
        copied += item["bytes"]
        _atomic_json(checkpoint_path, {"schema_version": 1, "source_path": str(source), "files_completed": completed, "bytes_completed": copied})
        emit("progress", {"unit": "files", "completed": index + 1, "total": len(entries), "bytes_completed": copied, "total_bytes": total, "current_file": item["path"]})
    metadata = inspect_dataset(staging, completed)
    result = {"status": "completed", "module_version": VERSION, "manifest_path": manifest_path.relative_to(root).as_posix(), "dataset_path": target.relative_to(root).as_posix(), "file_count": len(completed), "total_bytes": total, "metadata": metadata, "source_is_provenance_only": True}
    manifest = {"schema_version": 1, "module_version": VERSION, "source_path": str(source), "created_at": datetime.now(timezone.utc).isoformat(), "files": completed, "result": result}
    _atomic_json(manifest_path, manifest)
    os.replace(staging, target)
    emit("artifact", {"path": result["manifest_path"], "kind": "demo_manifest"})
    return result



def audit(ctx: dict) -> dict:
    """Audit the completed copy and sample real video frames if ffmpeg is configured."""
    import subprocess
    import yaml
    root = Path(ctx["root"]).resolve()
    output = _inside(root, Path(ctx["output_dir"]))
    output.mkdir(parents=True, exist_ok=True)
    inputs = ctx.get("input", {})
    imported = inputs.get("import_result", {})
    if not imported:
        dependencies = ctx.get("dependencies", {})
        imported = dependencies.get("demo_import", {})
        if isinstance(imported, dict) and "result" in imported:
            imported = imported["result"]
    if not imported.get("manifest_path"):
        raise ValueError("Completed import_result.manifest_path is required")
    manifest_path = _inside(root, root / imported["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    dataset = _inside(root, root / imported["dataset_path"])
    metadata = imported.get("metadata", {})
    cancel = ctx.get("check_cancel", lambda: None)
    emit = ctx.get("emit", lambda *args: None)
    findings = []
    checked = 0
    for index, item in enumerate(manifest["files"]):
        cancel()
        path = dataset / item["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(dataset) or not path.is_file():
            raise ValueError("Imported file escaped dataset or is missing")
        if _hash(path, cancel) != item["sha256"]:
            raise ValueError(f"Imported file integrity failed: {item['path']}")
        checked += 1
        emit("progress", {"unit": "files", "completed": index + 1, "total": len(manifest["files"]), "current_file": item["path"]})
    if not metadata.get("fps"):
        findings.append({"category": "data_quality", "severity": "warning", "code": "fps_unverified", "message": "Recording rate is not established by imported metadata."})
    if metadata.get("depth_status") == "lossy_encoded_not_metric_verified":
        findings.append({"category": "data_quality", "severity": "warning", "code": "lossy_depth_encoding", "message": "Source explicitly declares lossy/8-bit depth encoding; do not use decoded pixels as exact metric depth or invalid mask.", "evidence": "meta/nero_depth_encoding.json"})
    findings.append({"category": "data_quality", "severity": "review", "code": "depth_unverified", "message": "Metric depth scale, clipping and synchronization require explicit verification; RGB video is insufficient."})
    for archive in metadata.get("arrays", []):
        for array in archive.get("arrays", []):
            if "O" in array.get("dtype", ""):
                findings.append({"category": "data_quality", "severity": "warning", "code": "object_array_not_loaded", "message": f"{array['name']}: object/pickle arrays are not loaded."})
    config_path = root / "config/environments.yaml"
    environments = yaml.safe_load(config_path.read_text()).get("environments", {}) if config_path.is_file() else {}
    executable = environments.get("ffmpeg_executable", "")
    video_reports = []
    frames = []
    video_budget = max(0, min(int(inputs.get("max_sample_videos", 3)), 8))
    frame_budget = max(1, min(int(inputs.get("frames_per_video", 6)), 12))
    selected_episode = int(inputs.get("episode_index", 0))
    episodes = metadata.get("episodes", [])
    episode = next((record for record in episodes if record.get("episode_index") == selected_episode), None)
    if episodes and episode is None:
        raise ValueError("Selected episode_index not present in imported episode metadata")
    selected_videos = []
    seen_streams = set()
    priorities = {"observation.images.front": 0, "observation.images.left_wrist": 1, "observation.images.right_wrist": 2, "observation.images.side": 3}
    for record in sorted(metadata.get("videos", []), key=lambda r: (priorities.get(r.get("stream"), 4), r["path"])):
        if record.get("is_depth"):
            continue
        stream = record.get("stream")
        if episode and stream:
            prefix = "videos/" + stream
            chunk, file = episode.get(prefix + "/chunk_index"), episode.get(prefix + "/file_index")
            if chunk is None or file is None:
                continue
            expected = f"videos/{stream}/chunk-{int(chunk):03d}/file-{int(file):03d}.mp4"
            if record["path"] != expected:
                continue
        key = stream or record["path"]
        if key not in seen_streams:
            selected_videos.append(record)
            seen_streams.add(key)
    for video_index, record in enumerate(selected_videos[:video_budget]):
        cancel()
        source = _inside(dataset, dataset / record["path"])
        report = {"path": record["path"], "decoded": False, "samples": []}
        if not executable:
            report["status"] = "needs_ffmpeg_configuration"
            video_reports.append(report)
            continue
        ffmpeg = Path(executable)
        ffprobe = ffmpeg.with_name("ffprobe")
        if not ffmpeg.is_file() or not os.access(ffmpeg, os.X_OK) or not ffprobe.is_file():
            report.update(status="configured_ffmpeg_unavailable", error="Configure a runnable ffmpeg with sibling ffprobe")
            video_reports.append(report)
            continue
        try:
            probe = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,avg_frame_rate,nb_frames,duration:format=duration", "-of", "json", str(source)], capture_output=True, text=True, timeout=30, check=True)
            details = json.loads(probe.stdout)
            stream = details.get("streams", [{}])[0]
            duration = float(stream.get("duration") or details.get("format", {}).get("duration") or 0)
            if not 0 < duration < 86400:
                raise ValueError("Video duration missing or outside sampling budget")
            start, end = 0., duration
            stream_name = record.get("stream")
            if episode and stream_name:
                prefix = "videos/" + stream_name
                start = float(episode.get(prefix + "/from_timestamp", 0.))
                end = float(episode.get(prefix + "/to_timestamp", duration))
                if not 0 <= start < end <= duration + .1:
                    raise ValueError("Episode video interval outside video duration")
                end = min(end, duration)
            report.update(status="decoded_samples", decoded=True, stream=stream, duration_seconds=duration, sampled_interval_seconds=[start, end], episode_index=selected_episode if episode else None)
            directory = output / f"video_{video_index:02d}"
            directory.mkdir(exist_ok=True)
            for index in range(frame_budget):
                cancel()
                timestamp = start + (end - start) * (index + .5) / frame_budget
                frame = directory / f"frame_{index:03d}.jpg"
                subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.6f}", "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2", str(frame)], capture_output=True, timeout=45, check=True)
                if not frame.is_file():
                    raise ValueError("Decoder did not produce requested sample")
                item = {"path": frame.relative_to(root).as_posix(), "source_video": record["path"], "requested_timestamp_seconds": timestamp, "timestamp_precision": "ffmpeg accurate seek; not sensor capture calibration", "sha256": _hash(frame)}
                report["samples"].append(item)
                frames.append(item)
                emit("artifact", {"path": item["path"], "kind": "demo_frame"})
            sheet = directory / "contact_sheet.jpg"
            subprocess.run([str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1", "-i", str(directory / "frame_%03d.jpg"), "-vf", f"scale=320:-2,tile=3x{(frame_budget + 2) // 3}", "-frames:v", "1", str(sheet)], capture_output=True, timeout=45, check=True)
            if sheet.is_file():
                report["contact_sheet"] = sheet.relative_to(root).as_posix()
                emit("artifact", {"path": report["contact_sheet"], "kind": "demo_contact_sheet"})
        except (ValueError, OSError, subprocess.SubprocessError, IndexError) as exc:
            report.update(status="decode_failed", error=str(exc)[:1000])
        video_reports.append(report)
    report = {"schema_version": 1, "module_version": VERSION, "status": "completed", "quality_state": "requires_review", "integrity_files_checked": checked, "metadata": metadata, "findings": findings, "videos": video_reports, "sample_frames": frames, "semantic_analysis_performed": False, "metric_depth_verified": False, "episode_index": selected_episode if episode else None, "sample_selection_scope": "episode_metadata_interval" if episode else "first_segment_per_rgb_stream_unverified", "cross_view_capture_sync_verified": False}
    path = output / "data_audit.json"
    _atomic_json(path, report)
    return report | {"report_path": path.relative_to(root).as_posix()}
