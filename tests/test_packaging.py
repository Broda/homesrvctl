from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


EXPECTED_JEKYLL_TEMPLATE_FILES = {
    "homesrvctl/templates/app/jekyll/docker-compose.yml.j2",
    "homesrvctl/templates/app/jekyll/dockerignore.j2",
    "homesrvctl/templates/app/jekyll/Dockerfile.j2",
    "homesrvctl/templates/app/jekyll/README.md.j2",
    "homesrvctl/templates/app/jekyll/site.Gemfile.j2",
    "homesrvctl/templates/app/jekyll/site._config.yml.j2",
    "homesrvctl/templates/app/jekyll/site.index.md.j2",
}


def test_build_artifacts_include_jekyll_template_assets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "dist"

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(outdir)],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheel_path = next(outdir.glob("homesrvctl-*.whl"))
    sdist_path = next(outdir.glob("homesrvctl-*.tar.gz"))

    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_names = set(wheel.namelist())
    missing_from_wheel = EXPECTED_JEKYLL_TEMPLATE_FILES - wheel_names
    assert not missing_from_wheel, f"wheel is missing Jekyll template assets: {sorted(missing_from_wheel)}"

    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_names = set(sdist.getnames())
    sdist_paths = {name.split("/", 1)[1] for name in sdist_names if "/" in name}
    missing_from_sdist = EXPECTED_JEKYLL_TEMPLATE_FILES - sdist_paths
    assert not missing_from_sdist, f"sdist is missing Jekyll template assets: {sorted(missing_from_sdist)}"
