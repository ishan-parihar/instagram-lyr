"""Safe package-manager delegation for the ``update`` command."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import site
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _tool_environment(path: Path, root: Path, package: str) -> bool:
    """Match the tool's own isolated environment, not one of its dependencies."""
    try:
        relative = path.relative_to(root.expanduser().resolve())
    except ValueError:
        return False
    return bool(relative.parts) and _normalize(relative.parts[0]) == _normalize(package)


def _manager(
    distribution: importlib.metadata.Distribution, package: str
) -> tuple[str | None, str | None]:
    """Return the owner manager and a refusal reason for unsupported installs."""
    location = Path(str(distribution.locate_file(""))).resolve()
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        try:
            details = json.loads(direct_url)
        except json.JSONDecodeError:
            details = {}
        if details.get("dir_info", {}).get("editable"):
            return None, "This is an editable/source install. Update its checkout and reinstall it."
        source_url = details.get("url", "")
        if urlsplit(source_url).scheme == "file" and not details.get("vcs_info"):
            source_path = unquote(urlsplit(source_url).path)
            return None, (
                f"This package came from a local checkout at {source_path}. Pull that checkout, "
                "then reinstall it with the same package manager."
            )

    home = Path.home()
    pipx_roots = [Path(os.environ["PIPX_HOME"])] if os.environ.get("PIPX_HOME") else []
    pipx_roots.append(home / ".local/share/pipx")
    if local_appdata := os.environ.get("LOCALAPPDATA"):
        pipx_roots.append(Path(local_appdata) / "pipx")
    if any(_tool_environment(location, root / "venvs", package) for root in pipx_roots):
        return "pipx", None

    uv_roots = [Path(os.environ["UV_TOOL_DIR"])] if os.environ.get("UV_TOOL_DIR") else []
    uv_roots.append(home / ".local/share/uv/tools")
    if appdata := os.environ.get("APPDATA"):
        uv_roots.append(Path(appdata) / "uv/tools")
    if any(_tool_environment(location, root, package) for root in uv_roots):
        return "uv", None

    if sys.prefix != sys.base_prefix and _under(location, Path(sys.prefix)):
        return "pip-venv", None

    user_site = site.getusersitepackages()
    if isinstance(user_site, str) and _under(location, Path(user_site)):
        return "pip-user", None

    return None, "The install owner is not identifiable; refusing to replace package files."


def _normalize(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _text_latest(output: str, package: str) -> str | None:
    wanted = _normalize(package)
    version = r"(?:v)?(\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?)"
    for line in output.splitlines():
        normalized_line = _normalize(line)
        if wanted not in normalized_line:
            continue
        # Check for arrow/transition format first: X.Y.Z -> Y.Z or X.Y.Z → Y.Z
        # This must be checked before the general "labeled" patterns below
        transition = re.search(rf"{version}\s*(?:->|→)\s*{version}", line)
        if transition:
            # Return the "to" version (group 2), or the version after the arrow
            return transition.group(2)
        # Match all "labeled" format variants after arrow check:
        # - [latest: vX.Y.Z] or [available: vX.Y.Z] (uv tool list --outdated)
        # - latest: vX.Y.Z or latest=vX.Y.Z
        # - available=X.Y.Z
        labeled = re.search(
            rf"\[latest:\s*{version}\]|\[available:\s*{version}\]"
            rf"|latest:\s*{version}|available\s*=\s*{version}",
            line,
            re.IGNORECASE,
        )
        if labeled:
            # Extract version from the captured group; group(1) is the version number
            m = re.search(rf"{version}", labeled.group(0))
            if m:
                return m.group(1)
    return None


def _check(owner: str, package: str) -> tuple[bool | None, str | None]:
    if owner == "uv":
        command = ["uv", "tool", "list", "--outdated"]
    elif owner == "pipx":
        command = ["pipx", "list", "--outdated"]
    else:
        command = [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"]

    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if completed.returncode != 0:
        return None, None

    if owner.startswith("pip-"):
        try:
            rows = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None, None
        for row in rows:
            if _normalize(str(row.get("name", ""))) == _normalize(package):
                return True, str(row.get("latest_version", "")) or None
        return False, None

    latest = _text_latest(completed.stdout + "\n" + completed.stderr, package)
    if latest:
        return True, latest
    output = _normalize(completed.stdout + "\n" + completed.stderr)
    if _normalize(package) not in output:
        return False, None
    return None, None


def run_update(args: Sequence[str], *, application: str, package: str) -> int:
    """Delegate update/check to the manager that owns this installation."""
    parser = argparse.ArgumentParser(prog=f"{application} update")
    parser.add_argument(
        "--check", action="store_true", help="check for an update without installing"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    options = parser.parse_args(list(args))
    # The refusal below is shared by every verb; report the action actually asked
    # for so the JSON field is true rather than always claiming "check".
    action = "check" if options.check else "update"

    try:
        distribution = importlib.metadata.distribution(package)
        current = distribution.version
    except importlib.metadata.PackageNotFoundError:
        distribution = None
        current = "unknown"

    owner: str | None
    reason: str | None
    if distribution is None:
        reason = (
            "Installed package metadata was not found; reinstall with the documented installer."
        )
        owner = None
    else:
        owner, reason = _manager(distribution, package)

    if owner is None:
        _emit(
            options.json,
            application,
            current,
            None,
            action,
            "refused",
            reason or "Unknown install owner.",
        )
        return 1

    if options.check:
        if owner.startswith("pip-") and distribution is not None and _is_vcs_install(distribution):
            _emit(
                options.json,
                application,
                current,
                None,
                "check",
                "unknown",
                "This pip install tracks a VCS source, so pip's package-index check cannot "
                "establish whether its branch changed. Run update to ask pip to fetch that "
                "source.",
            )
            return 0
        outdated, available = _check(owner, package)
        result = "available" if outdated else "current" if outdated is False else "unknown"
        detail = (
            f"{application} {current} is current."
            if outdated is False
            else f"{application} {current} can be updated to {available}."
            if outdated
            else f"Could not determine the latest version; run `{_upgrade_hint(owner, package)}`."
        )
        _emit(options.json, application, current, available, "check", result, detail)
        return 0 if outdated is not None else 1

    command = _upgrade_command(owner, package, distribution)
    if command is None:
        _emit(
            options.json,
            application,
            current,
            None,
            "refused",
            "This install source cannot be upgraded safely in place; reinstall from the "
            "documented source.",
        )
        return 1
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _emit(options.json, application, current, None, "update", "failed", str(exc))
        return 1

    if completed.returncode != 0:
        detail = (
            completed.stderr or completed.stdout
        ).strip() or f"{command[0]} exited {completed.returncode}"
        _emit(options.json, application, current, None, "update", "failed", detail)
        return 1

    try:
        updated = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        updated = "unknown"
    _emit(options.json, application, current, updated, "update", "updated")
    return 0


def _upgrade_command(
    owner: str,
    package: str,
    distribution: importlib.metadata.Distribution | None = None,
) -> list[str] | None:
    if owner == "uv":
        return ["uv", "tool", "upgrade", package]
    if owner == "pipx":
        return ["pipx", "upgrade", package]
    if distribution is not None:
        direct_url = distribution.read_text("direct_url.json")
        if direct_url:
            try:
                details = json.loads(direct_url)
            except json.JSONDecodeError:
                return None
            vcs = details.get("vcs_info")
            source_url = details.get("url", "")
            if not vcs or not source_url.startswith(("https://", "ssh://", "git+ssh://")):
                return None
            parsed_url = urlsplit(source_url)
            if parsed_url.username or parsed_url.password or parsed_url.query:
                return None
            requirement = source_url if source_url.startswith("git+") else f"git+{source_url}"
            revision = vcs.get("requested_revision")
            if revision:
                if not re.fullmatch(r"[A-Za-z0-9._/-]+", revision):
                    return None
                requirement = f"{requirement}@{revision}"
            return [sys.executable, "-m", "pip", "install", "--upgrade", requirement]
    return [sys.executable, "-m", "pip", "install", "--upgrade", package]


def _is_vcs_install(distribution: importlib.metadata.Distribution) -> bool:
    direct_url = distribution.read_text("direct_url.json")
    if not direct_url:
        return False
    try:
        return bool(json.loads(direct_url).get("vcs_info"))
    except json.JSONDecodeError:
        return False


def _upgrade_hint(owner: str, package: str) -> str:
    if owner == "uv":
        return f"uv tool upgrade {package}"
    if owner == "pipx":
        return f"pipx upgrade {package}"
    return f"{sys.executable} -m pip install --upgrade {package}"


def _emit(
    use_json: bool,
    application: str,
    installed: str,
    available: str | None,
    action: str,
    result: str,
    detail: str | None = None,
) -> None:
    if use_json:
        payload = {
            "application": application,
            "installed_version": installed,
            "available_version": available,
            "channel": "stable",
            "action": action,
            "result": result,
            "service_restarted": False,
        }
        if detail:
            payload["detail"] = detail
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{application}: {detail or result}")
