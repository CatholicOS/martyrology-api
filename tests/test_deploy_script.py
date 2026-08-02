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


def _extract_permission_selfcheck() -> str:
    """Pulls the UNREADABLE=... / if / echo / die / fi block that follows the
    chgrp/chmod pair out of deploy.sh's current source. The `find` invocation
    spans several lines, so the block runs from the `UNREADABLE=` line to the
    first `fi` after it."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(
        i for i, line in enumerate(lines) if line.startswith('UNREADABLE="$(find "$RELEASE"')
    )
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start_idx : end_idx + 1])


def _extract_permission_selfcheck_block() -> tuple[str, str]:
    """The two pieces the permission tests below splice: the literal chmod
    line from deploy.sh, and the self-check block that follows it."""
    return _extract_line('chmod -R u+rwX,g+rX,o-rwx "$RELEASE"'), _extract_permission_selfcheck()


def _own_group() -> str:
    """The test user's own primary group, used as a stand-in for the
    martyrology service group: it is the one group this process is
    guaranteed to be able to chgrp to and to be a member of."""
    return subprocess.run(["id", "-gn"], capture_output=True, text=True, check=True).stdout.strip()


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


def test_tightens_the_uploaded_bundle_to_0600(tmp_path: Path):
    """The bundle scp'd into incoming/ *contains* the licensed corpus, and
    scp writes it with whatever umask the deploy user's ssh session had.
    deploy.sh only deletes it on the success path, so a deploy that fails
    anywhere after upload would leave a permissive copy sitting in
    incoming/ until the next successful deploy. Tightening happens as soon
    as the path is known to exist -- before the checksum is even read --
    so it covers every failure path after that point, which is all of them.

    Uses --dry-run: it returns before the venv/systemd-dependent work this
    suite cannot reach, but after the chmod.
    """
    app = _app_dir(tmp_path)
    bundle = _bundle(app, "1.0.0")
    checksum = bundle.parent / f"{bundle.name}.sha256"
    bundle.chmod(0o644)
    checksum.chmod(0o644)

    result = _run(app, "--dry-run", "1.0.0")

    assert result.returncode == 0, result.stderr
    assert bundle.stat().st_mode & 0o777 == 0o600, "bundle must not be readable by other accounts"
    assert checksum.stat().st_mode & 0o777 == 0o600


def test_chmod_grants_group_access_and_denies_every_other_account(tmp_path: Path):
    """Regression test for the release-tree permission fix, which has to get
    two opposite things right at once.

    The service account (martyrology) must be able to read the tree: it
    shares no *primary* group with the deploy user, and on a host with a
    restrictive umask (e.g. UMASK 027) or non-permissive tar member modes
    from the CI runner, the deploy completes and reports success while the
    unit dies with "Permission denied" on ExecStart.

    And nothing else must: releases/<v>/data/texts holds the licensed
    martyrology-texts corpus, and the VPS is Plesk-managed, where every
    other hosted subscription runs its own non-chrooted uid on the same
    box. The earlier `chmod -R a+rX` fixed the first problem by creating
    the second -- it published the corpus to every local account, which is
    precisely what the private-submodule architecture exists to prevent.
    So this asserts the group bits are present AND that no "other" bit
    survives anywhere.

    Reaching this line through a real end-to-end `deploy.sh <version>` run
    would require a working `python3.12 -m venv` plus a real installable
    martyrology-api wheel for `pip install --no-index`, neither available
    in this suite (the same constraint noted for the venv/systemd-dependent
    paths elsewhere in this file). Instead this splices the literal chmod
    line out of deploy.sh's current source (extracted at test-run time, not
    hand-duplicated, via the same pattern used for the rollback and smoke
    harnesses above) and runs it against a tree built both too tight (a
    0600 file, 0700 dirs) and too loose (a 0644 file, a 0755 dir), so a
    revert to `a+rX` fails on the loose entries and a removal of the chmod
    entirely fails on the tight ones.

    Also proves the capital-X distinction the fix depends on: a plain data
    file with no execute bit anywhere must NOT gain one (that's what
    lowercase `x` would have done, making every JSON file "executable"),
    while a file that already had an owner execute bit does gain the group
    execute bit, and directories become group-traversable.
    """
    chmod_line = _extract_line('chmod -R u+rwX,g+rX,o-rwx "$RELEASE"')

    release = tmp_path / "release"
    (release / "sub").mkdir(parents=True)
    (release / "loose").mkdir()
    data_file = release / "sub" / "manifest.json"
    script_file = release / "sub" / "run.sh"
    corpus_file = release / "loose" / "01.json"

    data_file.write_text("{}", encoding="utf-8")
    script_file.write_text("#!/bin/sh\n", encoding="utf-8")
    corpus_file.write_text("{}", encoding="utf-8")
    script_file.chmod(0o700)
    data_file.chmod(0o600)
    corpus_file.chmod(0o644)  # deliberately world-readable going in
    (release / "sub").chmod(0o700)
    (release / "loose").chmod(0o755)  # deliberately world-traversable going in
    release.chmod(0o700)

    result = subprocess.run(
        ["bash", "-c", f"RELEASE={release}\n{chmod_line}\n"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    for path in (release, release / "sub", release / "loose"):
        mode = path.stat().st_mode & 0o777
        assert mode & 0o050 == 0o050, f"{path} must be group r-x, got {mode:04o}"
        assert mode & 0o007 == 0, f"{path} must deny all other access, got {mode:04o}"
    for path in (data_file, script_file, corpus_file):
        mode = path.stat().st_mode & 0o777
        assert mode & 0o040 == 0o040, f"{path} must be group-readable, got {mode:04o}"
        assert mode & 0o007 == 0, (
            f"{path} must deny all other access (the licensed corpus must not be "
            f"world-readable on a shared host), got {mode:04o}"
        )
    assert data_file.stat().st_mode & 0o010 == 0, (
        "a data file with no execute bit must gain group-read only, "
        "never group-execute (that would mean lowercase x was used, not X)"
    )
    assert script_file.stat().st_mode & 0o010 == 0o010, (
        "a file that already had an owner execute bit must gain group-execute too"
    )


def _run_selfcheck(release: Path, service_group: str) -> subprocess.CompletedProcess[str]:
    """Runs deploy.sh's literal permission self-check block against a tree,
    with $SERVICE_GROUP bound to the given group. `REACHED END` is echoed
    afterwards so a check that fails to abort is caught rather than read as
    a pass."""
    die_fn = _extract_die_function()
    selfcheck = _extract_permission_selfcheck()
    script = (
        f"RELEASE={release}\nSERVICE_GROUP={service_group}\n"
        f'{die_fn}\n{selfcheck}\necho "REACHED END" >&2\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_permission_selfcheck_fails_loudly_on_a_group_unreadable_tree(tmp_path: Path):
    """The chmod above is the fix; this exercises the belt-and-braces
    self-check that follows it in deploy.sh (the UNREADABLE=... / die
    block), on its own, against a tree that was deliberately left
    non-traversable by the service group -- standing in for the chmod
    silently not taking full effect (e.g. a filesystem quirk, or a later
    code change that adds a step after the chmod without re-running it).
    Splices the literal die() function and the literal self-check block out
    of deploy.sh's current source, same rationale as the harnesses above: a
    revert of either piece is what makes this test fail, not a
    hand-duplicated stand-in that could drift from the real script.
    """
    release = tmp_path / "release"
    (release / "locked").mkdir(parents=True)
    (release / "locked").chmod(0o700)  # not group-traversable, deliberately
    release.chmod(0o750)

    result = _run_selfcheck(release, _own_group())

    assert result.returncode != 0, result.stderr
    assert "not group-readable" in result.stderr
    assert "REACHED END" not in result.stderr


def test_permission_selfcheck_fails_loudly_on_a_world_readable_tree(tmp_path: Path):
    """The other half, and the one that matters for the licensing exposure:
    a tree the service account can read perfectly well, but which every
    other local account can read too. Under the previous `a+rX` this was
    the *expected* state and the old self-check asserted it, so this test
    is what stops a revert to world-readable from passing silently.

    Deliberately group-correct throughout, so the only reason it can fail
    is the "other" bits -- if this test passes, it is not passing by
    accident of some unrelated tightness.
    """
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    corpus = release / "data" / "01.json"
    corpus.write_text("{}", encoding="utf-8")
    corpus.chmod(0o644)
    (release / "data").chmod(0o755)
    release.chmod(0o755)

    result = _run_selfcheck(release, _own_group())

    assert result.returncode != 0, result.stderr
    assert "other-access denied" in result.stderr
    assert str(corpus) in result.stderr
    assert "REACHED END" not in result.stderr


def test_permission_selfcheck_fails_loudly_when_the_tree_is_not_in_the_service_group(
    tmp_path: Path,
):
    """Group bits are only worth anything if the group is the one the
    service account is in. `root` stands in for "some group that is not
    $SERVICE_GROUP": it exists on every Linux host and the tree is
    certainly not in it, so the ! -group arm must fire. Without this arm a
    tree left in the deploy user's own primary group -- what happens if
    releases/'s setgid bit is lost and the chgrp is dropped -- would sail
    through with textbook-correct 0750/0640 modes and be unreadable to the
    service account at runtime.
    """
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    (release / "data" / "01.json").write_text("{}", encoding="utf-8")
    subprocess.run(
        ["bash", "-c", f'chmod -R u+rwX,g+rX,o-rwx "{release}"'],
        capture_output=True,
        text=True,
        check=True,
    )

    result = _run_selfcheck(release, "root")

    assert result.returncode != 0, result.stderr
    assert "not group-readable" in result.stderr
    assert "REACHED END" not in result.stderr


def test_permission_selfcheck_passes_once_chmod_has_run(tmp_path: Path):
    """Companion positive control: the same self-check block, on a tree
    that is both too tight (0700 subdir) and too loose (0644 file) to
    begin with, but this time preceded by the real chmod line -- proving
    the two pieces work together as they do in the real script, not just
    each in isolation, and that the check is satisfiable at all rather
    than failing unconditionally.

    A dangling symlink is included on purpose: a symlink's own mode is
    always lrwxrwxrwx on Linux and chmod -R does not follow it, so without
    the `! -type l` exclusion in the self-check every real release would
    fail here on its venv's python symlink.
    """
    chmod_line, selfcheck = _extract_permission_selfcheck_block()
    die_fn = _extract_die_function()

    release = tmp_path / "release"
    (release / "locked").mkdir(parents=True)
    loose = release / "loose.json"
    loose.write_text("{}", encoding="utf-8")
    loose.chmod(0o644)
    (release / "locked").chmod(0o700)
    (release / "python").symlink_to("/usr/bin/python3.12")
    release.chmod(0o755)

    script = (
        f"RELEASE={release}\nSERVICE_GROUP={_own_group()}\n"
        f'{die_fn}\n{chmod_line}\n{selfcheck}\necho "REACHED END" >&2\n'
    )
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
