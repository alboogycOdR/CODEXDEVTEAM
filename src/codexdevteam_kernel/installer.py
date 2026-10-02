"""Safe, inactive-by-default installer for fresh projects."""

import json
import hashlib
import os
import secrets
import shutil
from pathlib import Path
from typing import Mapping

from . import __version__
from .onboarding import OnboardingMode, inspect_project
from .sync import SyncConflict, apply_framework_sync, plan_framework_sync


class InstallationConflict(RuntimeError):
    """Raised when installation cannot preserve existing project ownership."""


def install_fresh_project(root: str | Path,
                          framework_files: Mapping[str, bytes | str]) -> Path:
    """Install framework-owned files atomically without activating a HEAD.

    The installer only accepts a truly fresh project and refuses any existing
    `.codexdevteam` directory, even an empty one, to avoid adopting project data
    implicitly. Framework files must use the managed framework prefix.
    """
    return _install_in_mode(root, framework_files, expected=OnboardingMode.FRESH,
                            installed_from="fresh", path_prefix=".codexdevteam/framework/")


def install_devdepartment_sidecar(root: str | Path,
                                  adapter_files: Mapping[str, bytes | str]) -> Path:
    """Install only new CODEXDEVTEAM sidecar files beside an incumbent system."""
    return _install_in_mode(
        root, adapter_files, expected=OnboardingMode.DEVDEPARTMENT_SIDECAR,
        installed_from="devdepartment_sidecar",
        path_prefix=".codexdevteam/framework/sidecar/")


def _install_in_mode(root: str | Path, files: Mapping[str, bytes | str], *,
                     expected: OnboardingMode, installed_from: str,
                     path_prefix: str) -> Path:
    project = Path(root).resolve()
    inspection = inspect_project(project)
    if inspection.mode is not expected:
        raise InstallationConflict(
            f"installer requires {expected.value} mode; detected {inspection.mode.value}")
    destination = project / ".codexdevteam"
    if destination.exists() or destination.is_symlink():
        raise InstallationConflict(".codexdevteam already exists; refusing to adopt or overwrite project state")
    if not files:
        raise ValueError("installation requires at least one framework-owned file")
    invalid = [path for path in files if not isinstance(path, str) or not path.startswith(path_prefix)]
    if invalid:
        raise SyncConflict(f"installer accepts only paths below {path_prefix}: " + ", ".join(map(str, invalid)))
    nonce = secrets.token_hex(8)
    staging = project / f".codexdevteam-install-{nonce}"
    staging.mkdir()
    try:
        managed_files = {path: value.encode("utf-8") if isinstance(value, str) else bytes(value)
                         for path, value in files.items()}
        manifest_path = path_prefix + ".managed-files.json"
        if manifest_path in managed_files:
            raise SyncConflict("installer reserves the managed-files manifest path")
        manifest = _managed_manifest(managed_files)
        managed_files[manifest_path] = manifest
        plan = plan_framework_sync(staging, managed_files, mode=expected)
        if plan.conflicts:
            raise SyncConflict("invalid fresh framework layout: " + "; ".join(plan.conflicts))
        apply_framework_sync(plan)
        marker = {
            "protocol_version": 1,
            "framework_version": __version__,
            "active_head": None,
            "activated": False,
            "integration_mode": installed_from,
            "installed_from": installed_from,
        }
        package_root = staging / ".codexdevteam"
        (package_root / "installation.json").write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if destination.exists() or destination.is_symlink():
            raise InstallationConflict(".codexdevteam appeared during installation")
        os.rename(package_root, destination)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def upgrade_codexdevteam_project(root: str | Path,
                                 desired_files: Mapping[str, bytes | str]) -> tuple[str, ...]:
    """Upgrade managed framework files by recorded hashes; preserve project and activation state."""
    project = Path(root).resolve()
    inspection = inspect_project(project)
    marker_path = project / ".codexdevteam" / "installation.json"
    if any(path.is_symlink() for path in (project / ".codexdevteam", marker_path)):
        raise InstallationConflict("installation metadata cannot be reached through a symlink")
    if inspection.mode not in {OnboardingMode.CODEXDEVTEAM_UPGRADE,
                               OnboardingMode.DEVDEPARTMENT_SIDECAR}:
        raise InstallationConflict(
            f"upgrade requires an installed CODEXDEVTEAM project; detected {inspection.mode.value}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise InstallationConflict("installation marker is missing or invalid") from exc
    integration = marker.get("integration_mode")
    if integration == "fresh":
        prefix = ".codexdevteam/framework/"
    elif integration == "devdepartment_sidecar":
        prefix = ".codexdevteam/framework/sidecar/"
    else:
        raise InstallationConflict("installation mode is unknown; refusing framework upgrade")
    manifest_path = prefix + ".managed-files.json"
    manifest_file = project / manifest_path
    managed_parents = [project / ".codexdevteam", project / ".codexdevteam" / "framework"]
    if prefix.endswith("sidecar/"):
        managed_parents.append(project / ".codexdevteam" / "framework" / "sidecar")
    if manifest_file.is_symlink() or any(path.is_symlink() for path in managed_parents):
        raise InstallationConflict("managed-files manifest path cannot contain symlinks")
    try:
        manifest_bytes = manifest_file.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise InstallationConflict("managed-files manifest is missing or invalid") from exc
    if (not isinstance(manifest, dict) or manifest.get("protocol_version") != 1
            or not isinstance(manifest.get("files"), dict)):
        raise InstallationConflict("managed-files manifest has an unsupported schema")
    prior_hashes = manifest["files"]
    for path, digest in prior_hashes.items():
        if (not isinstance(path, str) or not path.startswith(prefix)
                or not isinstance(digest, str) or len(digest) != 64):
            raise InstallationConflict("managed-files manifest contains an invalid path or hash")
    if not desired_files:
        raise ValueError("upgrade requires the complete desired framework file set")
    removed = set(prior_hashes) - set(desired_files)
    if removed:
        raise SyncConflict("upgrade cannot silently drop managed files: "
                           + ", ".join(sorted(removed)))
    incoming: dict[str, bytes] = {}
    for path, content in desired_files.items():
        if not isinstance(path, str) or not path.startswith(prefix) or path == manifest_path:
            raise SyncConflict(f"upgrade accepts only paths below {prefix}")
        incoming[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    next_manifest = _managed_manifest(incoming)
    incoming[manifest_path] = next_manifest
    known_hashes = dict(prior_hashes)
    known_hashes[manifest_path] = hashlib.sha256(manifest_bytes).hexdigest()
    plan = plan_framework_sync(project, incoming, prior_hashes=known_hashes,
                               mode=inspection.mode)
    if plan.conflicts:
        raise SyncConflict("upgrade has unresolved ownership conflicts: "
                           + "; ".join(plan.conflicts))
    return apply_framework_sync(plan)


def _managed_manifest(files: Mapping[str, bytes]) -> bytes:
    payload = {
        "protocol_version": 1,
        "framework_version": __version__,
        "files": {path: hashlib.sha256(content).hexdigest()
                  for path, content in sorted(files.items())},
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
