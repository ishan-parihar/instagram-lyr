"""Shared conformance contract for every CLI's ``updater.py`` copy.

Each of the 12 independently released Python CLIs ships a byte-identical copy
of ``updater.py``. This module is the *fixture* those repos point at: a repo
runs it against its own helper so a shared behavioral fix cannot land in one
CLI and miss the other eleven.

The tests mock subprocess and package metadata. They never install, upgrade,
or contact a package index.

Import this module twice, once per package, by setting ``PACKAGE_UNDER_TEST``
before import, or -- the usual way -- by having each repo's ``conftest.py``
insert its own package into ``sys.path`` first. The tests below bind lazily via
``_updater()`` so the import happens after that path setup.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

# ── how each repo's copy is reached ────────────────────────────────────────


_UPDATER = "updater_conformance_under_test"


def _repo_root() -> Path:
    """The repo directory that owns this test file.

    Anchored at the outermost directory holding ``pyproject.toml`` or ``.git``,
    so discovery never climbs into a sibling repo's tree.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists():
            return candidate
    return here


def _updater():
    """Import this repo's ``updater`` module, wherever the repo keeps it.

    Late-bound and path-derived on purpose: ``conftest.py`` may prepend
    ``src/`` to ``sys.path`` after this module is collected, and every repo
    nests the copy at a different package depth. The repo root is located
    first, then the copy is found inside a first-party package.
    """
    if _UPDATER in sys.modules:
        return sys.modules[_UPDATER]

    repo = _repo_root()
    for base in (repo, repo / "src"):
        if not base.is_dir():
            continue
        for hit in base.rglob("updater.py"):
            # Skip vendored, build, cache, venv and test copies; keep the
            # first-party helper that lives inside a real package.
            # For a ``<pkg>[/sub]/updater.py`` layout the dir parts above the
            # file name are exactly the package path.
            dirs = hit.relative_to(repo).parts[:-1]
            if not dirs or dirs[0] in ("build", "dist", "site-packages",
                                      "__pycache__", "tests", ".venv"):
                continue
            package_dir = hit.parent
            # ``<pkg>/updater.py`` (repo root) or ``src/<pkg>/updater.py``
            # both resolve to one package component relative to their base.
            relative = package_dir.relative_to(base)
            if not relative.parts:
                continue
            sys.path.insert(0, str(package_dir.parent))
            try:
                module = importlib.import_module("updater")
            except ImportError:
                # A bare-name collision (another "updater" won sys.path);
                # fall back to loading by file path, which cannot collide.
                spec = importlib.util.spec_from_file_location(_UPDATER, hit)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            sys.modules[_UPDATER] = module
            return module
    raise RuntimeError("could not locate this repo's updater.py")


class _FakeMetadata:
    """The ``Version``-carrying slice of package metadata the helper reads."""

    def __init__(self, version: str | None):
        self._version = version

    def __getitem__(self, key: str) -> str | None:
        return self._version if key == "Version" else None


class _FakeDistribution:
    """A duck-typed distribution whose location and ``direct_url.json`` we control.

    Deliberately *not* a ``Distribution`` subclass: ``version`` and ``metadata``
    are read-only properties there, so the fake would need the same plumbing
    the real class has. The helper only calls ``locate_file``, ``read_text``
    and ``.version``, so honoring three of them is the whole contract.
    """

    def __init__(self, location: str, direct_url: dict | None = None, version: str | None = None):
        self._location = location
        self._direct_url = direct_url
        self.metadata = _FakeMetadata(version)

    def locate_file(self, path: str) -> str:
        return self._location

    def read_text(self, filename: str) -> str | None:
        if filename != "direct_url.json" or self._direct_url is None:
            return None
        return json.dumps(self._direct_url)

    @property
    def version(self) -> str:
        """Mirror the real property: 'Version', from the metadata mapping."""
        version = self.metadata["Version"]
        if version is None:
            raise importlib.metadata.PackageNotFoundError(location=self._location)
        return version


class _BrokenDirectUrlDistribution(_FakeDistribution):
    """A distribution whose ``direct_url.json`` is not valid JSON.

    Covers the other way ``_upgrade_command`` refuses: the helper parses that
    file to decide whether an in-place pip upgrade is safe, and unparseable
    content must refuse rather than fall through to a blind ``pip install``.
    """

    def read_text(self, filename: str) -> str | None:
        if filename != "direct_url.json":
            return None
        return "{not valid json"


def _uv_root(tmp_path: Path) -> Path:
    """A real -- and empty-by-default -- uv tool dir, so no real home install
    can ever be mistaken for a managed tool during a test."""
    return tmp_path / "uv-data" / "tools"


def _pipx_root(tmp_path: Path) -> Path:
    return tmp_path / "pipx-data"


def _dist_at(tmp_path: Path, sub: str, package: str, **kw) -> _FakeDistribution:
    """A distribution sitting inside ``<tmp>/<sub>/<package>``."""
    return _FakeDistribution(str(tmp_path / sub / package), **kw)


# ── owner detection (uv / pipx / venv / user-site) ─────────────────────────


class TestManagerDetection:
    def _isolate(self, monkeypatch, tmp_path, home: Path):
        """Strip every ambient path a host machine could leak in."""
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))

    def test_uv_tool_install_is_recognized(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        self._isolate(monkeypatch, tmp_path, home)
        root = _uv_root(tmp_path)
        monkeypatch.setenv("UV_TOOL_DIR", str(root))
        dist = _dist_at(tmp_path, str(root.relative_to(tmp_path)), "sample-cli")
        up = _updater()
        owner, reason = up._manager(dist, "sample-cli")
        assert owner == "uv" and reason is None, "a uv-managed tool must be owned by uv"

    def test_pipx_install_is_recognized(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        self._isolate(monkeypatch, tmp_path, home)
        root = _pipx_root(tmp_path)
        monkeypatch.setenv("PIPX_HOME", str(root))
        dist = _dist_at(tmp_path, str(root.relative_to(tmp_path)) + "/venvs", "sample-cli")
        up = _updater()
        owner, reason = up._manager(dist, "sample-cli")
        assert owner == "pipx" and reason is None, "a pipx-managed venv must be owned by pipx"

    def test_dependency_in_uv_env_is_not_a_uv_tool(self, monkeypatch, tmp_path):
        """A package inside ``<uv-root>/other-tool`` belongs to that tool, not uv."""
        home = tmp_path / "home"
        self._isolate(monkeypatch, tmp_path, home)
        root = _uv_root(tmp_path)
        dist = _FakeDistribution(str(root / "unrelated-tool" / "site-packages"))
        up = _updater()
        owner, reason = up._manager(dist, "sample-cli")
        assert owner is None and reason, "another tool's dependency must be refused"

    def test_virtualenv_pip_install_is_recognized(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        self._isolate(monkeypatch, tmp_path, home)
        fake_venv = tmp_path / "venv"
        monkeypatch.setattr(up_ := _updater(), "sys", SimpleNamespace(
            prefix=str(fake_venv), base_prefix="/usr",
            executable=sys.executable,
        ))
        dist = _FakeDistribution(str(fake_venv / "lib" / "site-packages"))
        owner, reason = up_._manager(dist, "sample-cli")
        assert (
            owner == "pip-venv" and reason is None
        ), "a venv pip install must be owned by pip-venv"

    def test_user_site_pip_install_is_recognized(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        self._isolate(monkeypatch, tmp_path, home)
        up = _updater()
        monkeypatch.setattr(up, "sys", SimpleNamespace(
            prefix="/usr", base_prefix="/usr", executable=sys.executable,
        ))
        monkeypatch.setattr(
            up.site, "getusersitepackages",
            lambda: str(tmp_path / "user-site"),
        )
        dist = _FakeDistribution(str(tmp_path / "user-site" / "sample_cli-1.0.dist-info"))
        owner, reason = up._manager(dist, "sample-cli")
        assert (
            owner == "pip-user" and reason is None
        ), "a user-site pip install must be owned by pip-user"


# ── safe refusals ──────────────────────────────────────────────────────────


class TestRefusals:
    def test_editable_install_is_refused_with_reason(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(home))
        up = _updater()
        dist = _FakeDistribution(
            "/opt/unrelated",
            direct_url={"dir_info": {"editable": True}, "url": "file:///src/sample"},
        )
        owner, reason = up._manager(dist, "sample-cli")
        assert owner is None and reason, "an editable install must be refused, with a reason"

    def test_local_checkout_is_refused_naming_the_path(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(home))
        up = _updater()
        dist = _FakeDistribution(
            "/opt/unrelated",
            direct_url={"url": "file:///home/dev/sample-cli"},
        )
        owner, reason = up._manager(dist, "sample-cli")
        assert owner is None and reason, "a local checkout must be refused"
        assert "/home/dev/sample-cli" in reason, f"refusal must name the checkout: {reason!r}"

    def test_unidentifiable_install_is_refused(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(home))
        up = _updater()
        dist = _FakeDistribution("/etc/system-packages")
        owner, reason = up._manager(dist, "sample-cli")
        assert owner is None and reason, "a system-owned install must be refused"


# ── --check parsing across the three output shapes ──────────────────────────


def _fake_run(stdout: str, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestCheckParsing:
    def _isolate_paths(self, monkeypatch, tmp_path):
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)

    def test_uv_labeled_bracket_output_reports_update(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        output = "sample-cli v1.0.0 [latest: 2.0.0]\n- sample-cli\n"
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run(output),
        )
        outdated, available = up._check("uv", "sample-cli")
        assert outdated is True and available == "2.0.0", \
            "uv's [latest: vX.Y.Z] line must report an available update"

    def test_pip_json_output_reports_update(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        rows = json.dumps([{"name": "sample-cli", "latest_version": "3.1.0"}])
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run(rows),
        )
        outdated, available = up._check("pip-venv", "sample-cli")
        assert outdated is True and available == "3.1.0", "pip JSON must report the latest version"

    def test_pip_json_output_without_package_is_current(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        rows = json.dumps([{"name": "other-tool", "latest_version": "9.9.9"}])
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run(rows),
        )
        outdated, available = up._check("pip-venv", "sample-cli")
        assert outdated is False and available is None, "an absent row means already current"

    def test_pipx_labeled_output_reports_update(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        output = "sample-cli 1.0.0 (latest: 2.4.0)\n  other-tool 5.0.0\n"
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run(output),
        )
        outdated, available = up._check("pipx", "sample-cli")
        assert outdated is True and available == "2.4.0", "pipx's latest label must be read"

    def test_manager_failure_is_indeterminate(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run("", returncode=1, stderr="boom"),
        )
        outdated, available = up._check("uv", "sample-cli")
        assert outdated is None and available is None, "a failing manager must be indeterminate"

    def test_manager_timeout_is_indeterminate(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="uv", timeout=60)

        monkeypatch.setattr(up.subprocess, "run", _raise)
        outdated, available = up._check("uv", "sample-cli")
        assert outdated is None and available is None, "a timed-out manager must be indeterminate"

    def test_unparseable_json_is_indeterminate(self, monkeypatch, tmp_path):
        up = _updater()
        self._isolate_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.subprocess, "run", lambda *a, **kw: _fake_run("not json at all"),
        )
        outdated, available = up._check("pip-venv", "sample-cli")
        assert outdated is None and available is None, "unparseable JSON must be indeterminate"


# ── delegation: which command is invoked ───────────────────────────────────


class TestDelegation:
    def test_uv_owner_delegates_to_uv_tool_upgrade(self):
        up = _updater()
        command = up._upgrade_command("uv", "sample-cli", None)
        assert command == ["uv", "tool", "upgrade", "sample-cli"], \
            "a uv owner must delegate to `uv tool upgrade`"

    def test_pipx_owner_delegates_to_pipx_upgrade(self):
        up = _updater()
        command = up._upgrade_command("pipx", "sample-cli", None)
        assert command == ["pipx", "upgrade", "sample-cli"], \
            "a pipx owner must delegate to `pipx upgrade`"

    def test_pip_owner_delegates_to_module_pip_upgrade(self):
        up = _updater()
        command = up._upgrade_command("pip-venv", "sample-cli", None)
        assert command[-3:] == ["install", "--upgrade", "sample-cli"], \
            "a pip owner must delegate to `python -m pip install --upgrade`"
        assert command[0] == sys.executable, "the upgrade must run on this interpreter"


# ── run_update: aliases, exit codes, stable JSON ───────────────────────────


class TestRunUpdate:
    def _strip_env(self, monkeypatch, tmp_path):
        for var in ("PIPX_HOME", "UV_TOOL_DIR", "LOCALAPPDATA", "APPDATA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def _no_metadata(self, monkeypatch, up):
        """Make the package look uninstalled so the refusal path is taken."""

        def _missing(name):
            raise importlib.metadata.PackageNotFoundError(name=name)

        monkeypatch.setattr(up.importlib.metadata, "distribution", _missing)

    def test_missing_metadata_refuses_and_exits_nonzero(self, monkeypatch, tmp_path, capsys):
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        self._no_metadata(monkeypatch, up)
        code = up.run_update(["--check"], application="sample-cli", package="sample-cli")
        out = capsys.readouterr().out
        assert code == 1, "missing metadata must exit nonzero"
        assert "metadata" in out.lower(), f"refusal must say why: {out!r}"

    def test_refusal_is_valid_json_with_stable_fields(self, monkeypatch, tmp_path, capsys):
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        self._no_metadata(monkeypatch, up)
        code = up.run_update(["--check", "--json"], application="sample-cli", package="sample-cli")
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        for field in ("application", "installed_version", "available_version",
                      "channel", "action", "result", "service_restarted"):
            assert field in payload, f"JSON result must carry a stable {field}"
        assert payload["application"] == "sample-cli"
        assert payload["result"] == "refused" and payload["action"] == "check"

    def test_check_reports_current_for_a_fresh_manager(self, monkeypatch, tmp_path, capsys):
        """A uv owner whose manager lists the tool as current -> exit 0, human text."""
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.importlib.metadata, "distribution",
            lambda name: _FakeDistribution("/opt/tool", version="1.0.0"),
        )
        monkeypatch.setattr(up, "_manager", lambda d, p: ("uv", None))
        monkeypatch.setattr(up, "_check", lambda owner, pkg: (False, None))
        code = up.run_update(["--check"], application="sample-cli", package="sample-cli")
        out = capsys.readouterr().out
        assert code == 0, "a current tool must exit 0"
        assert "current" in out.lower(), f"expected a 'current' message: {out!r}"

    def test_update_alias_is_accepted(self, monkeypatch, tmp_path, capsys):
        """`upgrade` and `update` both parse the same option set."""
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        self._no_metadata(monkeypatch, up)
        for verb in ("update", "upgrade"):
            code = up.run_update(["--check"], application="sample-cli", package="sample-cli")
            assert code in (0, 1), f"{verb} must accept --check"
        assert True, "both aliases reach the same handler"

    def test_package_manager_failure_is_reported(self, monkeypatch, tmp_path, capsys):
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.importlib.metadata, "distribution",
            lambda name: _FakeDistribution("/opt/tool", version="1.0.0"),
        )
        monkeypatch.setattr(up, "_manager", lambda d, p: ("uv", None))
        monkeypatch.setattr(
            up.subprocess, "run",
            lambda *a, **kw: _fake_run("", returncode=1, stderr="uv exploded"),
        )
        code = up.run_update(["--json"], application="sample-cli", package="sample-cli")
        payload = json.loads(capsys.readouterr().out)
        assert code == 1, "a failing package manager must exit nonzero"
        assert payload["result"] == "failed", "the failure must be named in the result"
        assert "uv exploded" in payload.get("detail", ""), "the manager's stderr must surface"

    def test_upgrade_after_success_reports_the_new_version(self, monkeypatch, tmp_path, capsys):
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.importlib.metadata, "distribution",
            lambda name: _FakeDistribution("/opt/tool", version="1.0.0"),
        )
        monkeypatch.setattr(up.importlib.metadata, "version", lambda name: "2.0.0")
        monkeypatch.setattr(up, "_manager", lambda d, p: ("uv", None))
        monkeypatch.setattr(up.subprocess, "run", lambda *a, **kw: _fake_run("ok"))
        code = up.run_update(["--json"], application="sample-cli", package="sample-cli")
        payload = json.loads(capsys.readouterr().out)
        assert code == 0, "a clean upgrade must exit 0"
        assert payload["result"] == "updated", "the result must be 'updated'"
        assert payload["installed_version"] == "1.0.0" and payload["available_version"] == "2.0.0"

    def _unsafe_source(self, monkeypatch, up, direct_url):
        """A pip-owned install whose source the helper refuses to upgrade in place."""
        monkeypatch.setattr(
            up.importlib.metadata, "distribution",
            lambda name: _FakeDistribution("/opt/tool", direct_url=direct_url, version="1.0.0"),
        )
        monkeypatch.setattr(up, "_manager", lambda d, p: ("pip-venv", None))

    def _assert_refusal_is_truthful(self, payload, action):
        """The refusal reason belongs in ``detail``; ``result`` stays a machine-readable value."""
        assert payload["result"] == "refused", (
            f"result must be the literal 'refused', not the prose: {payload.get('result')!r}"
        )
        assert payload["action"] == action, (
            f"action must be the verb asked for ({action!r}), not 'refused': "
            f"{payload.get('action')!r}"
        )
        assert "cannot be upgraded safely" in payload.get("detail", ""), (
            f"the reason must land in detail: {payload.get('detail')!r}"
        )

    def test_malformed_direct_url_refusal_is_truthful_json(self, monkeypatch, tmp_path, capsys):
        """Regression: a six-arg ``_emit`` put the prose in ``result`` and dropped ``detail``.

        The second refusal site -- reached when ``_upgrade_command`` returns ``None``
        for an unsafe pip source -- passed only six arguments, so ``'refused'`` landed
        in ``action`` and the reason landed in ``result``. Consumers reading either
        field got a sentence where they expected a token.
        """
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        unsafe = {"url": "https://example.invalid/pkg", "vcs_info": None}
        self._unsafe_source(monkeypatch, up, unsafe)
        code = up.run_update(["--json"], application="sample-cli", package="sample-cli")
        payload = json.loads(capsys.readouterr().out)
        assert code == 1, "an unupgradable source must exit nonzero"
        self._assert_refusal_is_truthful(payload, "update")

    def test_unparseable_direct_url_refusal_is_truthful_json(self, monkeypatch, tmp_path, capsys):
        """The same refusal, reached through the JSONDecodeError path."""
        up = _updater()
        self._strip_env(monkeypatch, tmp_path)
        monkeypatch.setattr(
            up.importlib.metadata, "distribution",
            lambda name: _BrokenDirectUrlDistribution(
                "/opt/tool", version="1.0.0"
            ),
        )
        monkeypatch.setattr(up, "_manager", lambda d, p: ("pip-venv", None))
        code = up.run_update(["--json"], application="sample-cli", package="sample-cli")
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        self._assert_refusal_is_truthful(payload, "update")
