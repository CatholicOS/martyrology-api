import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "deploy.sh"


def _app_dir(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "incoming").mkdir(parents=True)
    (app / "releases").mkdir()
    return app


def _write_checksum(payload: Path) -> None:
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (payload.parent / f"{payload.name}.sha256").write_text(
        f"{digest}  {payload.name}\n", encoding="utf-8"
    )


def _bundle(app: Path, version: str, *, arcname: str = "manifest.json") -> Path:
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    source = app / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(source, arcname=arcname)
    source.unlink()
    _write_checksum(payload)
    return payload


def _bundle_with_traversal_and_filler(
    app: Path, version: str, *, filler_count: int = 20_000
) -> Path:
    """A traversal member first, then enough filler members that a naive
    `tar -tzf | grep -q` pipeline would see tar killed by SIGPIPE (exit 141)
    once grep matches and exits early — the regression case for the fix that
    captures `tar -tvzf` output before screening it, instead of piping into
    grep directly."""
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    with tarfile.open(payload, "w:gz") as archive:
        evil = tarfile.TarInfo(name="../escape.json")
        evil.size = 0
        archive.addfile(evil, io.BytesIO(b""))
        for i in range(filler_count):
            filler = tarfile.TarInfo(name=f"wheels/filler-{i}.whl")
            filler.size = 0
            archive.addfile(filler, io.BytesIO(b""))
    _write_checksum(payload)
    return payload


def _bundle_with_absolute_member(app: Path, version: str) -> Path:
    # tarfile.TarFile.add() normalizes away a leading "/" in arcname before
    # storing it, so an absolute member can only be produced by constructing
    # the TarInfo directly and calling addfile(), bypassing that
    # normalization the same way a hand-crafted malicious archive would.
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    with tarfile.open(payload, "w:gz") as archive:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))
    _write_checksum(payload)
    return payload


def _bundle_with_symlink(app: Path, version: str, *, target: str) -> Path:
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    source = app / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(source, arcname="manifest.json")
        link = tarfile.TarInfo(name="escape-link")
        link.type = tarfile.SYMTYPE
        link.linkname = target
        archive.addfile(link)
    source.unlink()
    _write_checksum(payload)
    return payload


def _run(app: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"PATH": "/usr/bin:/bin", "APP_DIR": str(app)},
        capture_output=True,
        text=True,
    )


def test_rejects_a_missing_version(tmp_path: Path):
    result = _run(_app_dir(tmp_path))
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_rejects_a_shell_metacharacter_version(tmp_path: Path):
    result = _run(_app_dir(tmp_path), "--dry-run", "1.0.0; rm -rf /")
    assert result.returncode != 0
    assert "suspicious version" in result.stderr


def test_rejects_a_missing_bundle(tmp_path: Path):
    result = _run(_app_dir(tmp_path), "--dry-run", "9.9.9")
    assert result.returncode != 0
    assert "bundle not found" in result.stderr


def test_rejects_a_checksum_mismatch(tmp_path: Path):
    app = _app_dir(tmp_path)
    bundle = _bundle(app, "1.0.0")
    bundle.write_bytes(bundle.read_bytes() + b"tampered")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr


def test_rejects_a_path_traversal_member(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0", arcname="../escape.json")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_dry_run_accepts_a_good_bundle(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert not (app / "releases" / "1.0.0").exists()


def test_rejects_a_multi_member_traversal_archive_without_sigpipe_masking(tmp_path: Path):
    # Regression test: a naive `tar -tzf "$BUNDLE" | grep -Eq ...` pipeline lets
    # grep exit as soon as it matches the first (evil) member, which closes the
    # pipe out from under a still-writing tar; under `pipefail` the resulting
    # SIGPIPE makes the whole pipeline non-zero, so `if pipeline; then die; fi`
    # sees a FALSE condition and the guard never fires. A single-member archive
    # does not reproduce this because tar finishes before grep can exit early.
    app = _app_dir(tmp_path)
    _bundle_with_traversal_and_filler(app, "1.0.0")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_rejects_an_absolute_path_member(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle_with_absolute_member(app, "1.0.0")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_rejects_a_symlink_member_escaping_the_release_tree(tmp_path: Path):
    # The target has a space, so the name/target screen above (which reads
    # only the last whitespace-delimited field of each listing line) sees
    # just "copy" and misses it; the dedicated link-target regex, which
    # matches on the text right after " -> " instead of a split field, still
    # catches it. This is the case that makes the dedicated symlink check
    # more than a duplicate of the name-based one.
    app = _app_dir(tmp_path)
    _bundle_with_symlink(app, "1.0.0", target="/etc/passwd copy")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


def test_accepts_a_symlink_member_that_stays_inside_the_release_tree(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle_with_symlink(app, "1.0.0", target="manifest.json")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout


def test_rejects_a_checksum_file_naming_a_different_bundle(tmp_path: Path):
    app = _app_dir(tmp_path)
    bundle = _bundle(app, "1.0.0")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (bundle.parent / f"{bundle.name}.sha256").write_text(
        f"{digest}  some-other-file.tar.gz\n", encoding="utf-8"
    )
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "checksum file names" in result.stderr


def test_rejects_redeploy_of_the_currently_active_version(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0")
    release_dir = app / "releases" / "1.0.0"
    release_dir.mkdir()
    (app / "current").symlink_to(release_dir)
    # No --dry-run: this must be rejected by the active-release guard before
    # any real install step (rm -rf/venv/sudo) is ever reached.
    result = _run(app, "1.0.0")
    assert result.returncode != 0
    assert "currently active release" in result.stderr
    assert release_dir.exists()


def test_dry_run_ignores_active_release_guard(tmp_path: Path):
    # --dry-run only verifies the bundle and never touches the filesystem, so
    # it is not subject to the active-release guard (nothing would be torn
    # down anyway).
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0")
    release_dir = app / "releases" / "1.0.0"
    release_dir.mkdir()
    (app / "current").symlink_to(release_dir)
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode == 0, result.stderr


def test_dry_run_flag_is_honoured_after_the_version(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0")
    result = _run(app, "1.0.0", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert not (app / "releases" / "1.0.0").exists()


def test_rejects_an_unrecognised_extra_argument(tmp_path: Path):
    app = _app_dir(tmp_path)
    result = _run(app, "--dry-run", "1.0.0", "extra")
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()
