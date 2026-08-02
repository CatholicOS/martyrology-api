import hashlib
import io
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


def _extract_line(exact_text: str) -> str:
    """Pulls one exact line out of deploy.sh's current source, stripped of
    leading indentation, at test-run time rather than hand-duplicating it —
    same rationale as _extract_rollback_harness_pieces below. Raises (and so
    fails the test) if the line is not found, e.g. because it was reworded
    or removed."""
    text = SCRIPT.read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if line.strip() == exact_text)
    return line.strip()


def _extract_die_function() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line == "die() {")
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start_idx : end_idx + 1])


def _extract_world_readability_selfcheck() -> str:
    """Pulls the UNREADABLE=... / if / echo / die / fi block that follows
    the `chmod -R a+rX "$RELEASE"` line out of deploy.sh's current source."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(
        i for i, line in enumerate(lines) if line.startswith('UNREADABLE="$(find "$RELEASE"')
    )
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start_idx : end_idx + 1])


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


def _extract_smoke_harness_pieces() -> tuple[str, list[str]]:
    """Pulls the smoke_cleanup() function body and every trap line that
    arms it out of deploy.sh's current source, for splicing into the
    smoke-phase signal harness below.

    The trap lines are matched loosely (any `trap ...` line mentioning
    smoke_cleanup, in source order) rather than by exact text, on purpose:
    that way a regression that keeps the handler but rewires the traps --
    e.g. back to a single `trap smoke_cleanup EXIT INT TERM`, or dropping
    the `exit 143` from the signal handler -- still splices cleanly into
    the harness and is caught by the harness's *behavioral* assertion,
    instead of failing early on a text lookup that proves nothing about
    what the script does at runtime.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line == "smoke_cleanup() {")
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i] == "}")
    function_text = "\n".join(lines[start_idx : end_idx + 1])

    trap_lines = [
        line.strip()
        for line in lines
        if line.strip().startswith("trap ") and "smoke_cleanup" in line
    ]
    assert trap_lines, "found no trap line arming smoke_cleanup in deploy.sh"
    return function_text, trap_lines


def _build_smoke_signal_harness(tmp_path: Path) -> Path:
    function_text, trap_lines = _extract_smoke_harness_pieces()
    harness = tmp_path / "smoke-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'SMOKE_LOG="$(mktemp)"\n'
        'SMOKE_PID=""\n'
        'echo "$SMOKE_LOG"\n'
        "\n"
        f"{function_text}\n"
        "\n" + "\n".join(trap_lines) + "\n"
        "\n"
        "sleep 2\n"
        # Stands in for everything deploy.sh does after a passing smoke
        # check: the flip, the systemctl restart, the prune, exit 0.
        'echo "harness: CONTINUED PAST SMOKE PHASE" >&2\n'
        "smoke_cleanup\n"
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


def test_rejects_a_hardlink_member_with_an_absolute_target(tmp_path: Path):
    # Real end-to-end test through the actual script, made possible by the
    # `-P` on deploy.sh's two *listing* captures. Without -P, GNU tar
    # rewrites a hard link target of "/etc/passwd" down to a harmless
    # "etc/passwd" in its own listing output before the screen can look at
    # it -- so the guard was silently depending on tar's sanitization, and
    # this case could previously only be tested white-box against the
    # regex. With -P the listing shows "... link to /etc/passwd" verbatim
    # and the real script rejects the real bundle.
    app = _app_dir(tmp_path)
    _bundle_with_hardlink(app, "1.0.0", target="/etc/passwd")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


def test_rejects_a_hardlink_member_with_a_parent_relative_target(tmp_path: Path):
    # Companion to the absolute case: GNU tar collapses "../../etc/passwd"
    # to "etc/passwd" in a non-`-P` listing too, so this is likewise only
    # reachable end-to-end because the listings are taken with -P.
    app = _app_dir(tmp_path)
    _bundle_with_hardlink(app, "1.0.0", target="../../etc/passwd")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "link pointing outside the release tree" in result.stderr


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


def test_chmod_normalises_world_permissions_on_the_release_tree(tmp_path: Path):
    """Regression test for the missing-world-bits fix: on a host with a
    restrictive umask (e.g. UMASK 027) or tar member modes that came out of
    the CI runner non-permissive, the release tree's directories and files
    would not be traversable/readable by the martyrology service account,
    which shares no group with the deploy user and depends entirely on
    world bits. The deploy completes and reports success; the unit then
    dies with "Permission denied" on ExecStart.

    Reaching this line through a real end-to-end `deploy.sh <version>` run
    would require a working `python3.12 -m venv` plus a real installable
    martyrology-api wheel for `pip install --no-index`, neither available
    in this suite (the same constraint noted for the venv/systemd-dependent
    paths elsewhere in this file). Instead this splices the literal
    `chmod -R a+rX "$RELEASE"` line out of deploy.sh's current source
    (extracted at test-run time, not hand-duplicated, via the same pattern
    used for the rollback and smoke harnesses above) and runs it directly
    against a tree built under a restrictive umask, so a revert of that
    exact line is what makes this test fail.

    Also proves the capital-X distinction the fix depends on: a plain data
    file with no execute bit anywhere must NOT gain one (that's what
    lowercase `x` would have done, making every JSON file "executable"),
    while a file that already had an owner execute bit does gain the
    world execute bit, and both directories become traversable.
    """
    chmod_line = _extract_line('chmod -R a+rX "$RELEASE"')

    release = tmp_path / "release"
    (release / "sub").mkdir(parents=True)
    data_file = release / "sub" / "manifest.json"
    script_file = release / "sub" / "run.sh"

    data_file.write_text("{}", encoding="utf-8")
    script_file.write_text("#!/bin/sh\n", encoding="utf-8")
    script_file.chmod(0o700)
    data_file.chmod(0o600)
    (release / "sub").chmod(0o700)
    release.chmod(0o700)

    result = subprocess.run(
        ["bash", "-c", f"RELEASE={release}\n{chmod_line}\n"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    assert release.stat().st_mode & 0o007 == 0o005, "release dir must be world r-x"
    assert (release / "sub").stat().st_mode & 0o007 == 0o005, "subdir must be world r-x"
    assert data_file.stat().st_mode & 0o007 == 0o004, (
        "a data file with no execute bit must gain world-read only, "
        "never world-execute (that would mean lowercase x was used, not X)"
    )
    assert script_file.stat().st_mode & 0o007 == 0o005, (
        "a file that already had an owner execute bit must gain world-execute too"
    )


def test_world_readability_selfcheck_fails_loudly_on_a_non_traversable_tree(tmp_path: Path):
    """The chmod above is the fix; this exercises the belt-and-braces
    self-check that follows it in deploy.sh (the UNREADABLE=... / die
    block), on its own, against a tree that was deliberately left
    non-traversable -- standing in for the chmod silently not taking full
    effect (e.g. a filesystem quirk, or a later code change that adds a
    step after the chmod without re-running it). Splices the literal
    die() function and the literal self-check block out of deploy.sh's
    current source, same rationale as the harnesses above: a revert of
    either piece is what makes this test fail, not a hand-duplicated
    stand-in that could drift from the real script.
    """
    die_fn = _extract_die_function()
    selfcheck = _extract_world_readability_selfcheck()

    release = tmp_path / "release"
    (release / "locked").mkdir(parents=True)
    (release / "locked").chmod(0o700)  # not world-traversable, deliberately
    release.chmod(0o755)

    script = f'RELEASE={release}\n{die_fn}\n{selfcheck}\necho "REACHED END" >&2\n'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode != 0, result.stderr
    assert "not fully world-readable" in result.stderr
    assert "REACHED END" not in result.stderr


def test_world_readability_selfcheck_passes_once_chmod_has_run(tmp_path: Path):
    """Companion positive control: the same self-check block, on the same
    kind of deliberately-locked-down tree, but this time preceded by the
    real chmod line -- proving the two pieces work together as they do in
    the real script, not just each in isolation."""
    chmod_line = _extract_line('chmod -R a+rX "$RELEASE"')
    die_fn = _extract_die_function()
    selfcheck = _extract_world_readability_selfcheck()

    release = tmp_path / "release"
    (release / "locked").mkdir(parents=True)
    (release / "locked").chmod(0o700)
    release.chmod(0o755)

    script = f'RELEASE={release}\n{die_fn}\n{chmod_line}\n{selfcheck}\necho "REACHED END" >&2\n'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "REACHED END" in result.stderr


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


def test_signal_during_smoke_phase_stops_the_deploy_instead_of_continuing(tmp_path: Path):
    """Regression test for: a bash trap for a signal does not terminate the
    script, it returns control to where execution was.

    With the smoke phase's cleanup registered as one
    `trap '<cleanup>' EXIT INT TERM`, a SIGTERM landing anywhere in the
    smoke window ran the cleanup and then carried straight on -- past the
    smoke phase, into the flip, the systemctl restart and the prune --
    finishing a cancelled deploy and exiting 0. With no trap installed at
    all (the state before that wiring was added) bash's default
    disposition exited 143, so the trap had actively turned a loud failure
    into a silent success. The fix registers INT/TERM separately with a
    handler that ends in an explicit `exit 143`.

    Same harness technique, and same limits, as
    test_signal_during_flip_window_rolls_back_instead_of_exiting_zero:
    reaching the real smoke phase needs a working venv (uvicorn, the
    app's data files), which is not available here, so this splices the
    actual smoke_cleanup() body and every trap line arming it -- extracted
    from deploy.sh at test-run time, not hand-duplicated -- into a
    standalone script whose `sleep 2` stands in for the smoke window
    (wait_healthy's `sleep 1` loop, or the curl|python editions pipeline
    that follows it), and whose "CONTINUED PAST SMOKE PHASE" line stands
    in for everything deploy.sh does after a passing smoke check.

    What it covers: that a TERM in the smoke window terminates the script
    with a non-zero status and never reaches the post-smoke work, and that
    the smoke log is still cleaned up on that path. What it does not
    cover: killing a real uvicorn child (SMOKE_PID is empty in the
    harness, so the `kill` is exercised only as a no-op), or the real
    flip/restart that the "CONTINUED PAST" marker stands for.
    """
    harness = _build_smoke_signal_harness(tmp_path)
    proc = subprocess.Popen(
        ["bash", str(harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.3)
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=5)

    assert "CONTINUED PAST SMOKE PHASE" not in stderr, (
        "the signal handler ran but execution continued past the smoke phase; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert proc.returncode == 143, (
        f"expected exit 143 after SIGTERM, got {proc.returncode}; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    smoke_log = Path(stdout.strip().splitlines()[0])
    assert not smoke_log.exists(), f"smoke log {smoke_log} was left behind after the signal"
