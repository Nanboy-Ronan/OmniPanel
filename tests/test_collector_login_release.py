"""The login helper's version, wheel name and SHA256 must agree everywhere they are pinned.

The committed files are templates (__WHEEL_URL__, __ORIGIN__); each deployment builds its
own packages with `python integrations/collector-login/build_connector.py --origin ...`.
These tests build into a temporary directory and check what a deployment would serve.
"""
import hashlib
import json
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations/collector-login"
ORIGIN = "https://data.example.org"
PIN = re.compile(r"omnipanel_collector_login-([0-9.]+)-py3-none-any\.whl#sha256=([0-9a-f]{64})")
# Template -> number of __WHEEL_URL__ placeholders (cli.json has one per platform).
TEMPLATES = {"connector/cli.json": 3, "enterprise-skill/scripts/repair.sh": 1, "enterprise-skill/scripts/repair.ps1": 1}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    try:
        subprocess.run([sys.executable, str(ROOT / "build_connector.py"), "--origin", ORIGIN, "--dist", str(dist)],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if "No module named" in exc.stderr or "setuptools" in exc.stderr:
            pytest.skip("wheel build backend unavailable: " + exc.stderr.strip().splitlines()[-1])
        raise
    return dist, json.loads((dist / "release.json").read_text())


def test_versions_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    init = (ROOT / "rpa_collector_login/__init__.py").read_text()
    meta = json.loads((ROOT / "connector/connector-meta.json").read_text())
    assert version == meta["version"]
    assert f'__version__ = "{version}"' in init


def test_templates_pin_nothing_and_only_have_placeholders():
    for name, count in TEMPLATES.items():
        text = (ROOT / name).read_bytes().decode("utf-8-sig")
        assert text.count("__WHEEL_URL__") == count, name
        assert not PIN.search(text), name


def test_every_pin_matches_the_built_wheel(built):
    dist, release = built
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert release["version"] == version
    assert release["wheel"] == f"omnipanel_collector_login-{version}-py3-none-any.whl"
    assert hashlib.sha256((dist / release["wheel"]).read_bytes()).hexdigest() == release["sha256"]
    assert [p.name for p in dist.glob("*.whl")] == [release["wheel"]]
    with zipfile.ZipFile(dist / "omnipanel-collector-login-connector.zip") as connector, \
            zipfile.ZipFile(dist / "omnipanel-collector-login-skill.zip") as skill:
        pinned = {
            "cli.json": connector.read("cli.json").decode(),
            "repair.sh": skill.read("omnipanel-collector-login/scripts/repair.sh").decode(),
            "repair.ps1": skill.read("omnipanel-collector-login/scripts/repair.ps1").decode("utf-8-sig"),
        }
    for name, text in pinned.items():
        assert set(PIN.findall(text)) == {(release["version"], release["sha256"])}, name
        assert ORIGIN + "/api/collector/repair/download/" + release["wheel"] in text, name
        assert "__WHEEL_URL__" not in text and "__ORIGIN__" not in text, name


def test_wheel_contains_current_source_and_origin(built):
    dist, release = built
    with zipfile.ZipFile(dist / release["wheel"]) as wheel:
        for name in ("rpa_collector_login/cli.py", "rpa_collector_login/__init__.py"):
            assert wheel.read(name).decode() == (ROOT / name).read_text(), name
        assert wheel.read("rpa_collector_login/deployment.py").decode() == f"DEFAULT_ORIGIN = {ORIGIN!r}\n"


def test_zips_contain_current_connector_and_skill(built):
    dist, _release = built
    connector = ROOT / "connector"
    with zipfile.ZipFile(dist / "omnipanel-collector-login-connector.zip") as archive:
        assert set(archive.namelist()) == {p.relative_to(connector).as_posix() for p in connector.rglob("*") if p.is_file()}
    skill = ROOT / "enterprise-skill"
    with zipfile.ZipFile(dist / "omnipanel-collector-login-skill.zip") as archive:
        expected = {"omnipanel-collector-login/" + p.relative_to(skill).as_posix() for p in skill.rglob("*") if p.is_file()}
        assert set(archive.namelist()) == expected
        # repair.ps1 keeps its BOM and CRLF line endings through templating.
        raw = archive.read("omnipanel-collector-login/scripts/repair.ps1")
        assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw


def test_windows_script_is_bom_utf8_and_runs_no_downloaded_script():
    raw = (ROOT / "enterprise-skill/scripts/repair.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "install.ps1" not in text and "& $installer" not in text
    assert "Get-FileHash" in text and "Expand-Archive" in text


def test_server_whitelist_serves_current_release(built, monkeypatch):
    from app.views import collector_repair
    dist, release = built
    monkeypatch.setattr(collector_repair, "HELPER_DIST", dist)
    assert collector_repair.helper_files() == {
        release["wheel"], "omnipanel-collector-login-connector.zip", "omnipanel-collector-login-skill.zip"}
