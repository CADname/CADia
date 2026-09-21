from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def test_core_release_version_metadata_is_consistent():
    init = (ROOT / "src" / "standalonecad" / "__init__.py").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    project_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert version == project_version == "2026.09.15"


def test_release_dependency_versions_match_verified_stack():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "cadquery==2.8.0" in req
    assert "vtk==9.6.2" in req
    assert "PySide6-Essentials==6.11.2" in req
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'cadquery==2.8.0' in pyproject
    assert 'vtk==9.6.2' in pyproject
    assert 'PySide6-Essentials==6.11.2' in pyproject
    assert 'requires-python = ">=3.11,<3.13"' in pyproject


def test_hackathon_web_release_metadata_is_consistent():
    package_json = json.loads((ROOT / "web" / "frontend" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "web" / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert package_json["version"] == "2026.09.15"
    assert package_lock["packages"][""]["version"] == "2026.09.15"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    devpost = (ROOT / "DEVPOST_SUBMISSION.md").read_text(encoding="utf-8")
    assert "AI-Native Editable CAD" in readme
    assert "real B-Rep CAD" in readme or "real parametric B-Rep" in readme
    assert "InfinityX Global Hackathon 2K26" in readme
    assert "https://github.com/CADname/CADia" in devpost
    assert "Add the final public or unlisted demo-video URL before submission." in devpost


def test_hackathon_release_does_not_ship_local_runtime_artifacts():
    forbidden_names = {".env", "node_modules", "dist"}
    for path in ROOT.rglob("*"):
        assert path.name not in forbidden_names, f"forbidden release artifact: {path}"
        assert not path.name.endswith(".pem"), f"private certificate/key candidate shipped: {path}"
        assert not path.name.endswith(".key"), f"private key candidate shipped: {path}"
