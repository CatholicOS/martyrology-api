import hashlib
import io
import re
import signal
import subprocess
import tarfile
import time
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


def _bundle_with_raw_member(
    app: Path, version: str, *, name: str, symlink_target: str | None = None
) -> Path:
    # Builds a TarInfo directly and calls addfile(), bypassing tarfile's own
    # arcname normalization (see _bundle_with_absolute_member above) so the
    # member name is stored exactly as given — leading "/", leading "../",
    # and embedded spaces included — the same way a hand-crafted malicious
    # archive would produce it.
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    with tarfile.open(payload, "w:gz") as archive:
        info = tarfile.TarInfo(name=name)
        if symlink_target is not None:
            info.type = tarfile.SYMTYPE
            info.linkname = symlink_target
            archive.addfile(info)
        else:
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


def _bundle_with_hardlink(app: Path, version: str, *, target: str) -> Path:
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    source = app / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(source, arcname="manifest.json")
        link = tarfile.TarInfo(name="sibling-hardlink")
        link.type = tarfile.LNKTYPE
        link.linkname = target
        archive.addfile(link)
    source.unlink()
    _write_checksum(payload)
    return payload


def _extract_link_regex() -> str:
    """Pulls the ERE used by the link-target screen directly out of
    deploy.sh's current source, so a white-box test of that regex cannot
    silently drift from what the script actually runs."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = 'die "bundle contains a link pointing outside the release tree"'
    marker_idx = next(i for i, line in enumerate(lines) if marker in line)
    grep_line = lines[marker_idx - 1]
    match = re.search(r"grep -Eq '(.+)' <<<", grep_line)
    assert match, f"could not find the link-screen grep line before: {grep_line!r}"
    return match.group(1)


def _grep_matches(pattern: str, text: str) -> bool:
    result = subprocess.run(["grep", "-Eq", pattern], input=text, text=True)
    return result.returncode == 0


def _extract_rollback_harness_pieces() -> tuple[str, str, str]:
    """Pulls the rollback_on_failure() function body and its two
    trap-arming lines directly out of deploy.sh's current source, for
    splicing into the signal-handling test harness below. Extracting at
    test-run time (instead of hand-duplicating the logic) means a later
    edit to those exact lines in deploy.sh changes what the harness
    exercises too."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line == "rollback_on_failure() {")
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i] == "}")
    function_text = "\n".join(lines[start_idx : end_idx + 1])

    exit_trap_line = next(line for line in lines if line.strip() == "trap rollback_on_failure EXIT")
    signal_trap_line = next(
        line for line in lines if line.strip() == "trap 'rollback_on_failure 143' INT TERM"
    )
    return function_text, exit_trap_line.strip(), signal_trap_line.strip()


def _build_signal_harness(tmp_path: Path) -> Path:
    function_text, exit_trap_line, signal_trap_line = _extract_rollback_harness_pieces()
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'VERSION="harness-test"\n'
        'PREVIOUS=""\n'
        "ROLLBACK_ARMED=0\n"
        "\n"
        f"{function_text}\n"
        "\n"
        "ROLLBACK_ARMED=1\n"
        f"{exit_trap_line}\n"
        f"{signal_trap_line}\n"
        "\n"
        "sleep 2\n"
        'echo "harness: sleep completed without a signal" >&2\n'
        "ROLLBACK_ARMED=0\n"
        "trap - EXIT INT TERM\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return harness


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
    # The member's own name ("escape-link") is innocuous; only its target
    # escapes, and a target with a space in it at that. The name-only screen
    # (plain `tar -t`) never sees link targets at all, by design, so it
    # cannot catch this regardless of whitespace; only the dedicated
    # verbose-listing check, which reads the text after " -> " directly
    # rather than splitting the line into fields, can.
    app = _app_dir(tmp_path)
    _bundle_with_symlink(app, "1.0.0", target="/etc/passwd copy")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


def test_rejects_a_symlink_whose_own_name_traverses(tmp_path: Path):
    # Regression test for the round-1 regression: the fix that captured
    # `tar -tvzf`'s *verbose* listing and screened `awk '{print $NF}'` over
    # it never screened a link's own member name at all, only its target —
    # for a symlink listing line ("name -> target"), $NF is the target, not
    # the name. A single-member archive isolates this from the link-target
    # check, which would otherwise also fire (on the target) and mask the
    # gap in the name check.
    app = _app_dir(tmp_path)
    _bundle_with_raw_member(
        app,
        "1.0.0",
        name="../../evil-name",
        symlink_target="benign-relative",
    )
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_rejects_a_traversal_member_with_a_space_in_its_name(tmp_path: Path):
    # Regression test: with the round-1 $NF-based screen, a name containing
    # a space was only screened by its last token ("sh"), missing the
    # leading "../../" entirely.
    app = _app_dir(tmp_path)
    _bundle_with_raw_member(app, "1.0.0", name="../../etc/cron.d/evil sh")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_rejects_an_absolute_member_with_a_space_in_its_name(tmp_path: Path):
    # Regression test: same $NF-splitting gap as above, for an absolute
    # path ("/etc/passwd x" was only screened as "x").
    app = _app_dir(tmp_path)
    _bundle_with_raw_member(app, "1.0.0", name="/etc/passwd x")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


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


def test_rejects_a_symlink_target_of_bare_dotdot(tmp_path: Path):
    # The round-2 regex required a trailing "/" after ".." (".*\.\./"), so a
    # target of exactly ".." -- which still walks up one directory -- was not
    # caught. Verified end-to-end via a real archive: GNU tar does not
    # sanitize symlink targets, so this reaches the check unmodified.
    app = _app_dir(tmp_path)
    _bundle_with_symlink(app, "1.0.0", target="..")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


def test_rejects_a_symlink_target_ending_in_dotdot_with_no_trailing_slash(tmp_path: Path):
    # Same gap, for a target ending in a "../"-less ".." component
    # ("a/.."), which also walks back up past "a".
    app = _app_dir(tmp_path)
    _bundle_with_symlink(app, "1.0.0", target="a/..")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


def test_accepts_a_hardlink_member_that_stays_inside_the_release_tree(tmp_path: Path):
    # Real integration positive control for the widened link regex: a
    # hardlink to a sibling file already in the bundle must not be
    # rejected as a false positive.
    app = _app_dir(tmp_path)
    _bundle_with_hardlink(app, "1.0.0", target="manifest.json")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout


def test_link_screen_regex_rejects_an_absolute_hardlink_target():
    """White-box test, not a full script/tar integration test -- and
    deliberately so, per investigation:

    GNU tar (1.35, as installed here; confirmed via `tar --version`)
    proactively normalizes a hard link's target during `tar -tv` listing
    itself, stripping any leading "/" and fully collapsing every ".."
    component before the line is ever displayed. Verified empirically: a
    hardlink target of "/etc/passwd", "../../etc/passwd", and even
    "safe/../../etc/passwd" (a non-leading traversal) all list as plain
    "etc/passwd", each with a `tar: Removing leading ...` warning on
    stderr. Running the actual martyrology-api deploy.sh --dry-run against
    a real archive built this way confirmed it: the bundle is accepted
    (exit 0) both before and after the round-3 fix, because the
    dangerous-looking text never reaches the screen in the first place on
    this tar implementation.

    That means no real archive built with this system's tar can
    discriminate old vs. new code here the way the other regression tests
    in this file do -- there is no "current form fails, fixed form
    passes" to demonstrate through the real script. Instead, this pulls
    the actual link-screening regex out of deploy.sh's current source
    (see _extract_link_regex) and runs it, via the real `grep -E` binary
    deploy.sh itself uses, against a hand-written listing line in the
    "name link to target" form a hardlink-to-/etc/passwd member would take
    under a tar implementation that does not normalize hard link targets
    (bsdtar/libarchive-based tars are known to differ here), or a future
    GNU tar release that stops doing so. This proves the regex extension
    itself is correct on its own terms; it does not prove today's real
    script rejects today's real archives built with today's tar, because
    -- on this tar implementation -- there is nothing dangerous left for
    it to reject by the time it looks.

    Paired with test_accepts_a_hardlink_member_that_stays_inside_the_release_tree
    (real integration, positive control) to also confirm the widened regex
    does not reject a benign hardlink.
    """
    regex = _extract_link_regex()
    dangerous_line = (
        "hrw-r--r-- 0/0               0 1970-01-01 01:00 evil-hardlink link to /etc/passwd"
    )
    safe_line = (
        "hrw-r--r-- 0/0               0 1970-01-01 01:00 sibling-hardlink link to manifest.json"
    )
    assert _grep_matches(regex, dangerous_line)
    assert not _grep_matches(regex, safe_line)


def test_rejects_a_corrupt_bundle_with_a_clear_message(tmp_path: Path):
    app = _app_dir(tmp_path)
    bundle = _bundle(app, "1.0.0")
    # Truncate well inside the gzip stream so `tar -tzf` fails outright
    # (rather than just listing an incomplete member); re-checksum the
    # truncated bytes so the corruption is caught by the tar-listing step
    # under test, not the earlier checksum-mismatch step.
    bundle.write_bytes(bundle.read_bytes()[:10])
    _write_checksum(bundle)
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "failed to list bundle contents" in result.stderr
    assert str(bundle) in result.stderr


def test_signal_during_flip_window_rolls_back_instead_of_exiting_zero(tmp_path: Path):
    """Regression test for: giving INT/TERM their own explicit status so a
    signal can never present as 0 and skip rollback.

    This does not exercise deploy.sh directly: reaching the real flip
    window requires a working venv, sudo, and systemd, none of which are
    available here (see the module-level constraints noted in the task
    brief). Per the coordinator's own guidance for this exact situation,
    it instead tests the handler semantics directly, via a small harness
    that splices the actual rollback_on_failure() function body and its
    two trap-arming lines -- extracted at test-run time from deploy.sh's
    current source, not hand-duplicated (see _extract_rollback_harness_pieces)
    -- into a standalone script with a bare `sleep 2` standing in for the
    `sleep 1` inside wait_healthy.

    This was verified to genuinely fail on a revert: temporarily restoring
    the old `trap rollback_on_failure EXIT INT TERM` wiring (no
    signal-specific argument) in deploy.sh and re-running only this test
    reproduced exactly the bug report's shape -- exit 0, no "rolling back"
    message in stderr -- before the wiring was restored to the fixed form.

    What this covers: the documented bash behavior that a signal trap does
    not run until the current foreground command completes, and that $?
    at that point reflects the completed command's own status, not the
    signal -- which is what let a TERM delivered to the script process
    alone (a CI cancellation, an ssh disconnect) read as a clean exit.
    What it does not cover: the real ln/mv/systemctl relink, or rolling
    back to an actual previous release -- PREVIOUS="" here, so the
    harness's own rollback_on_failure takes its "No previous release to
    roll back to" branch, which is enough to prove the handler received a
    non-zero status and attempted rollback at all, rather than silently
    exiting 0.
    """
    harness = _build_signal_harness(tmp_path)
    proc = subprocess.Popen(
        ["bash", str(harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.3)
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode != 0, (
        f"expected a non-zero exit after SIGTERM, got {proc.returncode}; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert "activation of harness-test failed" in stderr
    assert "rolling back" in stderr
