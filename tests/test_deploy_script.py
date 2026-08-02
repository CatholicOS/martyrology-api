import contextlib
import grp
import hashlib
import io
import signal
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

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
    first `fi` after it.

    Matched with lstrip() because the block now lives inside
    normalise_release_permissions() and is therefore indented; the extracted
    text is spliced into a `bash -c` script where leading whitespace is
    immaterial, so it is kept verbatim rather than dedented."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(
        i
        for i, line in enumerate(lines)
        if line.lstrip().startswith('UNREADABLE="$(find "$RELEASE"')
    )
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start_idx : end_idx + 1])


def _extract_permission_selfcheck_block() -> tuple[str, str]:
    """The two pieces the permission tests below splice: the literal chmod
    line from deploy.sh, and the self-check block that follows it."""
    return (
        _extract_line('chmod -R u+rwX,g+rX,o-rwx,a-s "$RELEASE"'),
        _extract_permission_selfcheck(),
    )


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


HARNESS_READY = "HARNESS ARMED"


def _wait_for_ready(proc: "subprocess.Popen[str]") -> None:
    """Block until the harness says its traps are armed.

    Replaces a fixed `time.sleep(0.3)` before the SIGTERM. That sleep was a
    race: under load (a parallel test run, a busy CI box) the signal could
    land before the traps existed, so bash's default disposition killed the
    harness outright and the test failed for a reason that has nothing to do
    with what it is testing. Worse, the same sleep sets the *upper* bound too
    -- there is no arrival time that is both certainly-after-arming and
    certainly-before the harness's `sleep 2` elapses. Reading the marker
    removes both ends of that guess.
    """
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert HARNESS_READY in line, f"harness never reported readiness; first stdout line: {line!r}"


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
        # Emitted immediately after the traps are armed, and before the sleep
        # that stands in for the flip window, so the test can signal at a
        # point it knows is inside that window rather than guessing at one.
        f'echo "{HARNESS_READY}"\n'
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


def _build_smoke_teardown_window_harness(tmp_path: Path) -> tuple[Path, Path]:
    """Harness for the teardown's own signal window: the stretch of
    smoke_cleanup() between the `trap -` and the `kill`/`rm`.

    That window is sub-millisecond in the real script, so it is widened here
    rather than raced: `kill` is overridden by a shell function (functions take
    precedence over builtins in bash) that first touches a marker file the test
    polls for, then blocks in `command sleep`. The test delivers its second
    signal while the teardown is inside that block.

    With `trap -` first, the traps are already gone by then, the signal gets
    bash's default disposition, and the process dies before `rm -f` ever
    runs -- leaving the temp log behind. With `trap -` last, the signal is
    still trapped, bash defers it to the end of the current foreground command
    and re-runs the idempotent handler, which cleans up.
    """
    function_text, trap_lines = _extract_smoke_harness_pieces()
    marker = tmp_path / "in-cleanup"
    harness = tmp_path / "smoke-window-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'SMOKE_LOG="$(mktemp)"\n'
        'SMOKE_PID=""\n'
        'echo "$SMOKE_LOG"\n'
        "kill() {\n"
        f'    : >"{marker}"\n'
        "    command sleep 1\n"
        "    return 0\n"
        "}\n"
        "\n"
        f"{function_text}\n"
        "\n" + "\n".join(trap_lines) + "\n"
        "\n"
        "sleep 1\n"
        'echo "harness: CONTINUED PAST SMOKE PHASE" >&2\n'
        "smoke_cleanup\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return harness, marker


def _extract_normalise_function() -> str:
    """Pulls normalise_release_permissions() -- the chgrp/chmod pair plus the
    self-check that proves they stuck -- out of deploy.sh's current source."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(
        i for i, line in enumerate(lines) if line == "normalise_release_permissions() {"
    )
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start_idx : end_idx + 1])


def _extract_app_dir_guard() -> str:
    """Pulls the `for GUARDED_DIR in ...; done` loop that asserts $APP_DIR,
    releases/ and incoming/ carry no "other" bits."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line.startswith("for GUARDED_DIR in "))
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "done")
    return "\n".join(lines[start_idx : end_idx + 1])


def _extract_version_assertion() -> str:
    """Pulls the post-restart served-version assertion (LIVE_HEALTH= through
    the SERVED_VERSION comparison) out of deploy.sh's current source."""
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line.startswith('LIVE_HEALTH="$(curl'))
    end_idx = next(
        i
        for i in range(start_idx + 1, len(lines))
        if lines[i].startswith('echo "$VERSION is live and healthy')
    )
    return "\n".join(lines[start_idx:end_idx]).rstrip()


@contextlib.contextmanager
def _healthz_server(payload: str) -> Iterator[int]:
    """A throwaway HTTP server answering every GET with `payload`, standing in
    for the restarted unit's /healthz. Yields the ephemeral port it bound."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _release_with_python(tmp_path: Path) -> Path:
    """A $RELEASE stub whose venv/bin/python is this interpreter -- enough for
    the served-version assertion, which uses the release's own python as its
    JSON parser."""
    release = tmp_path / "release"
    (release / "venv" / "bin").mkdir(parents=True)
    (release / "venv" / "bin" / "python").symlink_to(sys.executable)
    return release


def _run_version_assertion(
    tmp_path: Path, *, version: str, served: str
) -> subprocess.CompletedProcess[str]:
    release = _release_with_python(tmp_path)
    block = _extract_version_assertion()
    die_fn = _extract_die_function()
    with _healthz_server(served) as port:
        script = (
            "set -euo pipefail\n"
            f"VERSION={version}\nLIVE_PORT={port}\nRELEASE={release}\n"
            f'{die_fn}\n{block}\necho "REACHED END" >&2\n'
        )
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


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
    chmod_line = _extract_line('chmod -R u+rwX,g+rX,o-rwx,a-s "$RELEASE"')

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


def test_permission_selfcheck_fails_loudly_on_a_setuid_or_setgid_entry(tmp_path: Path):
    """Tar restores member modes verbatim, and those modes come from the CI
    runner, so a setuid or setgid bit that reached the staging tree lands
    intact in a release tree the whole service group can read -- and, for
    anything carrying an execute bit, run. A setgid *directory* is worse
    still: it keeps re-applying itself to everything written under it after
    the normalisation has already been asserted.

    The chmod's `a-s` is the fix; this is the arm that proves it stuck. The
    tree here is otherwise textbook-correct (group-readable, no other bits,
    right group), so `-perm /6000` is the only thing that can fire.
    """
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    setuid_file = release / "data" / "helper"
    setuid_file.write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run(
        ["bash", "-c", f'chmod -R u+rwX,g+rX,o-rwx "{release}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    setuid_file.chmod(setuid_file.stat().st_mode | 0o4000)

    result = _run_selfcheck(release, _own_group())

    assert result.returncode != 0, result.stderr
    assert str(setuid_file) in result.stderr
    assert "REACHED END" not in result.stderr


def test_chmod_clears_setuid_and_setgid_from_the_release_tree(tmp_path: Path):
    """The positive half of the arm above: the real chmod line, run against a
    tree carrying a setuid file, a setgid file and a setgid directory, must
    leave none of the three -- while still granting the group access the
    service account needs. Splices deploy.sh's literal chmod line, so dropping
    `a-s` from it fails here.
    """
    chmod_line = _extract_line('chmod -R u+rwX,g+rX,o-rwx,a-s "$RELEASE"')

    release = tmp_path / "release"
    setgid_dir = release / "data"
    setgid_dir.mkdir(parents=True)
    setuid_file = setgid_dir / "helper"
    setgid_file = setgid_dir / "other"
    setuid_file.write_text("#!/bin/sh\n", encoding="utf-8")
    setgid_file.write_text("{}", encoding="utf-8")
    setuid_file.chmod(0o4755)
    setgid_file.chmod(0o2644)
    setgid_dir.chmod(0o2755)

    result = subprocess.run(
        ["bash", "-c", f"RELEASE={release}\n{chmod_line}\n"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    for path in (setgid_dir, setuid_file, setgid_file):
        mode = path.stat().st_mode
        assert mode & 0o6000 == 0, f"{path} kept a setuid/setgid bit: {mode & 0o7777:04o}"
        assert mode & 0o040 == 0o040, f"{path} lost group read: {mode & 0o7777:04o}"


def test_permission_selfcheck_passes_on_a_release_created_under_a_setgid_parent(
    tmp_path: Path,
):
    """The production shape, and the way the `a-s`/`-perm /6000` pair could
    have turned into a check that fires on every deploy.

    `$APP_DIR/releases` is deliberately 2750 (setgid) so each release
    directory deploy.sh mkdir's under it inherits the `martyrology` group --
    and inherits the setgid bit along with it, as does every directory tar
    creates inside. So the very first real deploy arrives at the self-check
    with a tree full of setgid directories. The chmod's `a-s` is what clears
    them before the check looks; if it were dropped while the `-perm /6000`
    arm stayed, every deploy would fail here.

    Group ownership does not depend on the inherited bit: the chgrp -R
    immediately above the chmod sets it outright, and runs again after the
    smoke check, so stripping setgid costs nothing.
    """
    chmod_line, selfcheck = _extract_permission_selfcheck_block()
    die_fn = _extract_die_function()

    releases = tmp_path / "releases"
    releases.mkdir()
    releases.chmod(0o2750)
    assert releases.stat().st_mode & 0o2000, "setgid did not stick; test cannot prove anything"

    release = releases / "1.0.0"
    (release / "data").mkdir(parents=True)
    (release / "data" / "01.json").write_text("{}", encoding="utf-8")
    assert (release / "data").stat().st_mode & 0o2000, (
        "the release subtree did not inherit setgid from its parent; "
        "this test is not exercising the production shape"
    )

    script = (
        f"RELEASE={release}\nSERVICE_GROUP={_own_group()}\n"
        f'{die_fn}\n{chmod_line}\n{selfcheck}\necho "REACHED END" >&2\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "REACHED END" in result.stderr
    assert releases.stat().st_mode & 0o2000, (
        "the chmod is rooted at $RELEASE and must not have touched releases/'s setgid bit, "
        "which is what makes new release directories inherit the service group"
    )


def _a_group_the_tree_is_not_in(path: Path) -> str:
    """A group name that is definitely NOT the group owning `path`.

    Derived at runtime rather than hard-coded to `root`: the previous version
    of this test used `root` as its stand-in non-service group, which quietly
    inverts into a false pass the moment the suite runs somewhere root is the
    process's primary group (a container, a CI image running as uid 0) -- the
    tree would then genuinely be in `root` and the `! -group` arm it exists to
    exercise would never fire, while the test still went green for the wrong
    reason. Skips instead of guessing if the host has only one group defined.
    """
    tree_gid = path.stat().st_gid
    for entry in grp.getgrall():
        if entry.gr_gid != tree_gid:
            return entry.gr_name
    pytest.skip("host defines no group other than the one owning the test tree")


def test_permission_selfcheck_fails_loudly_when_the_tree_is_not_in_the_service_group(
    tmp_path: Path,
):
    """Group bits are only worth anything if the group is the one the
    service account is in, so the `! -group` arm must fire whenever the tree
    is in some other group. Without it a tree left in the deploy user's own
    primary group -- what happens if releases/'s setgid bit is lost and the
    chgrp is dropped -- would sail through with textbook-correct 0750/0640
    modes and be unreadable to the service account at runtime.
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
    result = _run_selfcheck(release, _a_group_the_tree_is_not_in(release))

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
    _wait_for_ready(proc)
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


def test_permission_normalisation_is_reapplied_after_the_smoke_check(tmp_path: Path):
    """The chgrp/chmod/self-check trio used to be a straight-line block that
    ran once, immediately after `pip install`. The smoke check that follows it
    then runs the app *out of that same tree*, so python can write
    `__pycache__` directories and `.pyc` files into it afterwards, with this
    process's umask rather than with the modes just asserted -- and what got
    symlinked into `current` was therefore not the tree that was checked.

    Two halves, because the fix has two parts and each can be reverted on its
    own:

    Structural -- that a `normalise_release_permissions` call actually sits
    between the smoke check's result and the `ln -sfn` flip. Deleting the
    second call site (leaving the function defined and called once) is exactly
    what the original defect was, and nothing behavioural in this suite can see
    it, because the real smoke check needs a working venv and uvicorn.

    Behavioural -- that the extracted function really is re-runnable and really
    does catch a tree dirtied after a first, passing normalisation: the
    self-check alone is run against the dirtied tree first (it must fail, so
    the check is proven to be what notices), then the whole function (it must
    fix and pass). If the check were toothless the first run would pass and
    this test would fail.
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    smoke_idx = next(i for i, line in enumerate(lines) if line.startswith('EDITIONS="$(curl'))
    flip_idx = next(i for i, line in enumerate(lines) if line.startswith('ln -sfn "$RELEASE"'))
    assert any(
        line.strip() == "normalise_release_permissions" for line in lines[smoke_idx:flip_idx]
    ), (
        "deploy.sh must re-normalise and re-assert $RELEASE's permissions after the "
        "smoke check has run the app out of that tree and before `current` is flipped"
    )

    normalise_fn = _extract_normalise_function()
    selfcheck = _extract_permission_selfcheck()
    die_fn = _extract_die_function()
    group = _own_group()

    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    (release / "data" / "01.json").write_text("{}", encoding="utf-8")

    preamble = f"RELEASE={release}\nSERVICE_GROUP={group}\n{die_fn}\n"

    first = subprocess.run(
        ["bash", "-c", f'{preamble}{normalise_fn}\nnormalise_release_permissions\necho "OK" >&2\n'],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr

    # Exactly what the smoke check leaves behind: a __pycache__ directory and a
    # .pyc, created with a permissive umask after the tree was normalised.
    pycache = release / "__pycache__"
    pycache.mkdir()
    pyc = pycache / "app.cpython-312.pyc"
    pyc.write_bytes(b"\x00")
    pyc.chmod(0o644)
    pycache.chmod(0o755)

    stale = subprocess.run(
        ["bash", "-c", f'{preamble}{selfcheck}\necho "REACHED END" >&2\n'],
        capture_output=True,
        text=True,
    )
    assert stale.returncode != 0, (
        "the self-check must notice a tree dirtied after the first normalisation; "
        f"stderr={stale.stderr!r}"
    )
    assert "other-access denied" in stale.stderr
    assert "REACHED END" not in stale.stderr

    second = subprocess.run(
        [
            "bash",
            "-c",
            f'{preamble}{normalise_fn}\nnormalise_release_permissions\necho "REACHED END" >&2\n',
        ],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stderr
    assert "REACHED END" in second.stderr
    for path in (pycache, pyc):
        assert path.stat().st_mode & 0o007 == 0, (
            f"{path} must deny all other access after re-normalisation, "
            f"got {path.stat().st_mode & 0o777:04o}"
        )


def _run_app_dir_guard(app: Path) -> subprocess.CompletedProcess[str]:
    """Runs deploy.sh's literal $APP_DIR/releases/incoming mode guard against a
    tree. `REACHED END` afterwards so a guard that fails to abort is caught
    rather than read as a pass."""
    die_fn = _extract_die_function()
    guard = _extract_app_dir_guard()
    script = f'APP_DIR={app}\n{die_fn}\n{guard}\necho "REACHED END" >&2\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _provisioned_app_dir(tmp_path: Path) -> Path:
    """$APP_DIR as setup-vps-deploy-user.sh leaves it: 0750 top and releases/,
    0700 incoming/."""
    app = tmp_path / "app"
    (app / "releases").mkdir(parents=True)
    (app / "incoming").mkdir()
    app.chmod(0o750)
    (app / "releases").chmod(0o2750)
    (app / "incoming").chmod(0o700)
    return app


def test_app_dir_guard_passes_on_a_correctly_provisioned_tree(tmp_path: Path):
    """Positive control: the guard has to be satisfiable by the modes the
    provisioning script actually sets, including releases/'s setgid bit, or it
    would fail every deploy and teach operators to ignore it."""
    result = _run_app_dir_guard(_provisioned_app_dir(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "REACHED END" in result.stderr


def test_app_dir_guard_fails_loudly_on_a_world_accessible_directory(tmp_path: Path):
    """The modes of $APP_DIR, releases/ and incoming/ were asserted once, at
    provisioning time, and never again. A later `chmod 0755 /opt/martyrology`
    -- an operator debugging a permission problem, a restore that did not
    preserve modes -- therefore went unnoticed by every subsequent deploy,
    which kept reporting a correctly locked-down *release* tree while the
    directory above it published that tree, and the bundle in incoming/, to
    every other uid on this shared Plesk host.

    Each of the three is loosened in turn, so a guard that checks only one (or
    that names the wrong one) fails here. `stat` reports the mode actually set,
    to make a test failure diagnosable.
    """
    for relative in ("", "releases", "incoming"):
        app = _provisioned_app_dir(tmp_path / f"case-{relative or 'top'}")
        target = app / relative if relative else app
        target.chmod(0o755)

        result = _run_app_dir_guard(app)

        assert result.returncode != 0, (
            f"{target} at 0755 must abort the deploy; stderr={result.stderr!r}"
        )
        assert str(target) in result.stderr, (
            f"the failure must name the path to fix; stderr={result.stderr!r}"
        )
        assert "REACHED END" not in result.stderr


def test_app_dir_guard_fails_loudly_on_a_missing_directory(tmp_path: Path):
    """`find` on a path that does not exist finds nothing, so a guard that
    only looked at find's *stdout* would read an unprovisioned tree as "no
    other bits" and let the deploy proceed -- a failure reporting success,
    which is the shape of defect this file exists to prevent.

    Two independent things stop that, and this asserts the outcome rather than
    which of them fired: the explicit `-d` test, and folding find's stderr into
    the same variable that is checked for emptiness. Dropping either alone
    still fails loudly; dropping both is what this test catches."""
    app = _provisioned_app_dir(tmp_path)
    (app / "incoming").rmdir()

    result = _run_app_dir_guard(app)

    assert result.returncode != 0, result.stderr
    assert str(app / "incoming") in result.stderr
    assert "REACHED END" not in result.stderr


def test_smoke_teardown_cleans_up_when_a_signal_lands_inside_it(tmp_path: Path):
    """Regression test for moving `trap - EXIT INT TERM` to the END of
    smoke_cleanup().

    With it first, there was a window inside the handler -- after the traps
    were cleared, before the `kill`/`rm` -- in which a signal got bash's
    default disposition and killed the script outright, orphaning the smoke
    uvicorn and leaving its temp log in /tmp. The body is idempotent
    (`kill … || true`, `rm -f`), so clearing last costs at most a harmless
    second run and closes the window.

    The real window is sub-millisecond, so the harness widens it rather than
    racing it: it splices the actual smoke_cleanup() body and its trap lines
    out of deploy.sh (same extraction as the smoke-signal test above) and
    overrides `kill` with a shell function that marks its entry and then
    blocks. The test sends one signal to enter the teardown and a second while
    it is inside that block.

    What it covers: that a signal delivered mid-teardown still ends with the
    temp log removed and a non-zero exit. What it does not cover -- same limits
    as the other harness tests -- killing a real uvicorn child, or any of the
    venv/systemd-dependent work the real script does around this phase.
    """
    harness, marker = _build_smoke_teardown_window_harness(tmp_path)
    proc = subprocess.Popen(
        ["bash", str(harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)

        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        assert marker.exists(), "the harness never entered smoke_cleanup"

        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on an unexpected hang
            proc.kill()
            proc.communicate()

    assert "CONTINUED PAST SMOKE PHASE" not in stderr, (
        f"execution continued past the smoke phase; stdout={stdout!r} stderr={stderr!r}"
    )
    assert proc.returncode != 0, (
        f"expected a non-zero exit after SIGTERM, got {proc.returncode}; "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    smoke_log = Path(stdout.strip().splitlines()[0])
    assert not smoke_log.exists(), (
        f"smoke log {smoke_log} was left behind by a signal delivered inside the teardown"
    )


def test_served_version_assertion_accepts_the_deployed_version(tmp_path: Path):
    """Positive control for the post-restart served-version check: a unit that
    really did swap to the new release reports it, and the deploy proceeds."""
    result = _run_version_assertion(tmp_path, version="0.1.0", served='{"version": "0.1.0"}')

    assert result.returncode == 0, result.stderr
    assert "REACHED END" in result.stderr


def test_served_version_assertion_strips_a_leading_v(tmp_path: Path):
    """The workflow passes the bare pyproject version, but the argument regex
    also accepts `v0.1.0` for a manual invocation, while HealthOut.version is
    always bare. Without the `${VERSION#v}` strip, every manual `deploy.sh
    v0.1.0` would roll back a perfectly good release."""
    result = _run_version_assertion(tmp_path, version="v0.1.0", served='{"version": "0.1.0"}')

    assert result.returncode == 0, result.stderr
    assert "REACHED END" in result.stderr


def test_served_version_assertion_rejects_a_stale_version(tmp_path: Path):
    """The defect this closes: `wait_healthy` only proves *something* answers
    /healthz on that port. A restart that did not actually swap processes --
    systemd reporting success while the old unit kept running, a flip that
    silently did not take -- leaves the previous release answering, and the
    deploy reported success for a version that was never activated. Failing
    here, before the rollback trap is disarmed, routes it to the rollback path
    instead."""
    result = _run_version_assertion(tmp_path, version="0.2.0", served='{"version": "0.1.0"}')

    assert result.returncode != 0, result.stderr
    assert "0.1.0" in result.stderr and "0.2.0" in result.stderr
    assert "REACHED END" not in result.stderr


def test_served_version_assertion_fails_on_unparseable_healthz(tmp_path: Path):
    """A /healthz that answers 200 with something that is not JSON must abort
    rather than compare against an empty string and, worse, match an empty
    $VERSION. The parse failure is its own diagnostic."""
    result = _run_version_assertion(tmp_path, version="0.1.0", served="<html>nope</html>")

    assert result.returncode != 0, result.stderr
    assert "REACHED END" not in result.stderr


SETUP_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "setup-vps-deploy-user.sh"
)


def _extract_setup_line(exact_text: str) -> str:
    line = next(
        line
        for line in SETUP_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() == exact_text
    )
    return line.strip()


def _extract_setup_world_check() -> str:
    """Pulls setup-vps-deploy-user.sh's "half two" block -- the find over the
    whole of $APP_DIR that refuses to finish provisioning while anything under
    it is reachable by an unrelated local account."""
    lines = SETUP_SCRIPT.read_text(encoding="utf-8").splitlines()
    start_idx = next(
        i for i, line in enumerate(lines) if line.startswith('WORLD_ACCESSIBLE="$(find "$APP_DIR"')
    )
    end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start_idx : end_idx + 1])


def _stale_incoming_tree(tmp_path: Path) -> tuple[Path, Path]:
    """$APP_DIR as a pre-fix failed deploy leaves it: correct directory modes
    throughout, but a 0644 bundle still sitting in incoming/ because deploy.sh
    only removes it on the success path."""
    app = tmp_path / "app"
    (app / "releases").mkdir(parents=True)
    (app / "incoming").mkdir()
    bundle = app / "incoming" / "martyrology-1.0.0-linux-x86_64-cp312.tar.gz"
    bundle.write_bytes(b"corpus")
    bundle.chmod(0o644)
    (app / "incoming" / f"{bundle.name}.sha256").write_text("x\n", encoding="utf-8")
    (app / "incoming" / f"{bundle.name}.sha256").chmod(0o644)
    (app / "incoming").chmod(0o700)
    (app / "releases").chmod(0o2750)
    app.chmod(0o750)
    return app, bundle


def test_provisioning_remediates_a_stale_world_readable_bundle_in_incoming(tmp_path: Path):
    """setup-vps-deploy-user.sh retracted world bits recursively from
    releases/ but not from incoming/, so a bundle left there by a pre-fix
    failed deploy kept the 0644 the deploy user's ssh umask gave it. The
    script's own half-two `find` then *detected* it and aborted provisioning
    with a message naming the file -- telling the operator to go and fix by
    hand something the script was already in the business of fixing.

    Both halves are asserted, in the order the script runs them, and the check
    is deliberately left untouched: the fix is the remediation, not a weaker
    check. First that the check really does fire on the stale tree (otherwise
    the second half would prove nothing), then that the spliced `chmod -R` line
    fixes it in place and the same check passes afterwards.

    `go-rwx`, not releases/'s `g+rX`: the bundle is a second copy of the
    licensed corpus in tarball form and only the deploy user ever needs it, so
    the group bits must come off too -- which the mode assertions below pin.
    """
    check = _extract_setup_world_check()
    app, bundle = _stale_incoming_tree(tmp_path)

    before = subprocess.run(
        ["bash", "-c", f'APP_DIR={app}\n{check}\necho "REACHED END" >&2\n'],
        capture_output=True,
        text=True,
    )
    assert before.returncode != 0, (
        f"a 0644 bundle in incoming/ must abort provisioning; stderr={before.stderr!r}"
    )
    assert str(bundle) in before.stderr
    assert "REACHED END" not in before.stderr

    chmod_line = _extract_setup_line('chmod -R u+rwX,go-rwx "$APP_DIR/incoming"')
    after = subprocess.run(
        ["bash", "-c", f'APP_DIR={app}\n{chmod_line}\n{check}\necho "REACHED END" >&2\n'],
        capture_output=True,
        text=True,
    )
    assert after.returncode == 0, after.stderr
    assert "REACHED END" in after.stderr

    assert bundle.stat().st_mode & 0o077 == 0, (
        "the stale bundle must end up owner-only, not merely non-world-readable: "
        f"got {bundle.stat().st_mode & 0o777:04o}"
    )
    assert bundle.stat().st_mode & 0o600 == 0o600, "the deploy user must still be able to read it"
    assert (app / "incoming").stat().st_mode & 0o777 == 0o700, "incoming/ itself must stay 0700"


TOKEN_WATCH_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "token-expiry-watch.yml"
)


def _extract_open_issue_function() -> str:
    """Pulls open_issue() out of the token-expiry watch workflow's `run:`
    block. Read as raw text and dedented rather than parsed as YAML, so the
    test needs no YAML dependency and sees exactly the bytes the workflow
    ships."""
    lines = TOKEN_WATCH_WORKFLOW.read_text(encoding="utf-8").splitlines()
    start_idx = next(i for i, line in enumerate(lines) if line.strip() == "open_issue() {")
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    end_idx = next(
        i
        for i in range(start_idx + 1, len(lines))
        if lines[i].strip() == "}" and len(lines[i]) - len(lines[i].lstrip()) == indent
    )
    return "\n".join(
        line[indent:] if line.strip() else "" for line in lines[start_idx : end_idx + 1]
    )


def test_token_watch_dedup_survives_a_payload_larger_than_the_pipe_buffer(tmp_path: Path):
    """Regression test for replacing `printf '%s\\n' "$existing" | grep -Fxq`
    with a here-string -- the same defect class already screened for in
    deploy.sh's tar listings.

    `grep -q` exits the moment it matches. If the writer still has data to
    push, and the payload is larger than the 64 KiB pipe buffer, the writer is
    still blocked in write() when the reader goes away and takes SIGPIPE. Under
    `pipefail` the pipeline then reports non-zero, the `if` reads FALSE, and the
    workflow files a duplicate issue -- precisely in the repository that has
    enough open issues for the payload to get that big. A here-string has no
    writer to signal.

    The harness stubs `gh` so `issue list` emits the matching title FIRST (so
    grep exits at once, with the maximum left to write) followed by well over
    64 KiB of filler titles, and so `issue create` announces itself loudly. The
    real open_issue() body is spliced out of the workflow, not re-typed.
    """
    open_issue = _extract_open_issue_function()
    harness = tmp_path / "dedup-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'REPO="owner/repo"\n'
        'TITLE="SUBMODULE_TOKEN expires soon (2026-09-01)"\n'
        "gh() {\n"
        '    if [ "${2:-}" = "list" ]; then\n'
        '        printf "%s\\n" "$TITLE"\n'
        "        for i in $(seq 1 8000); do\n"
        '            printf "filler issue title number %06d padded out a bit further\\n" "$i"\n'
        "        done\n"
        "        return 0\n"
        "    fi\n"
        '    echo "CREATED DUPLICATE ISSUE" >&2\n'
        "}\n"
        "\n"
        f"{open_issue}\n"
        "\n"
        'open_issue "$TITLE" "body"\n',
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=120)

    assert "CREATED DUPLICATE ISSUE" not in result.stderr, (
        "an already-open issue was re-filed: the dedup match lost to SIGPIPE on a "
        f"payload larger than the pipe buffer; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Issue already open" in result.stdout, result.stdout
    assert result.returncode == 0, result.stderr


DEPLOY_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"


def _extract_scp_destination_pieces() -> tuple[str, str]:
    """Pulls the upload step's scp destination expression and the
    REMOTE_APP_DIR assignment that must precede it out of deploy.yml.

    The assignment is found by searching *backwards* from the destination, so a
    revert that drops it from the upload step is not silently satisfied by the
    identical line in the "Activate release" step further down the file."""
    lines = DEPLOY_WORKFLOW.read_text(encoding="utf-8").splitlines()
    dest_idx = next(i for i, line in enumerate(lines) if line.strip().endswith('/incoming/"; then'))
    assign_idx = next(
        i for i in range(dest_idx, -1, -1) if lines[i].strip().startswith("REMOTE_APP_DIR=")
    )
    destination = lines[dest_idx].strip().removesuffix("; then")
    return lines[assign_idx].strip(), destination


def test_scp_destination_is_quoted_for_the_remote_shell(tmp_path: Path):
    """scp's destination is not a local path: everything after the colon is
    handed to the remote end and expanded by the remote shell, exactly like the
    ssh command in the "Activate release" step. The ssh step was already
    `printf %q`-safe; the scp destination interpolated $APP_DIR raw, so an
    APP_DIR containing whitespace was re-split remotely and the bundle landed
    somewhere other than where the deploy script then looked for it.

    Simulated rather than asserted textually: the two real lines are spliced
    out of the workflow, run with an APP_DIR containing a space, and the
    resulting remote path is then word-split by a second bash -- standing in
    for the remote shell. It must come back as exactly one word.
    """
    assignment, destination = _extract_scp_destination_pieces()
    harness = tmp_path / "scp-dest-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "APP_DIR='/opt/mar ty'\n"
        "VPS_USERNAME=deployer\n"
        "VPS_HOST=vps.example\n"
        f"{assignment}\n"
        f"DEST={destination}\n"
        'printf "%s" "${DEST#*:}"\n',
        encoding="utf-8",
    )
    remote_path = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, check=True
    ).stdout

    words = subprocess.run(
        ["bash", "-c", f'for w in {remote_path}; do printf "[%s]\\n" "$w"; done'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert words == ["[/opt/mar ty/incoming/]"], (
        "the remote shell must see the destination as one literal word; "
        f"got {words!r} from {remote_path!r}"
    )


def test_normalise_function_still_aborts_the_deploy_when_its_self_check_fails(tmp_path: Path):
    """The self-check moved inside a function when it was made re-runnable, and
    that move is exactly the kind that can turn a hard stop into a soft one: a
    `return` where an `exit` was meant, or a caller that swallows the status,
    would leave the deploy running on a tree that failed its own check --
    a failure reporting success, which is the recurring defect in this script's
    history.

    `chgrp` and `chmod` are stubbed to no-ops (shell functions take precedence
    over external commands) so the tree stays as built and the check has
    something to catch; the real function body is spliced out of deploy.sh
    unchanged. What is asserted is the *control flow*: the diagnostic is
    printed, the process exits non-zero, and nothing after the call runs.
    """
    normalise_fn = _extract_normalise_function()
    die_fn = _extract_die_function()

    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    corpus = release / "data" / "01.json"
    corpus.write_text("{}", encoding="utf-8")
    corpus.chmod(0o644)
    (release / "data").chmod(0o755)
    release.chmod(0o755)

    script = (
        "set -euo pipefail\n"
        f"RELEASE={release}\nSERVICE_GROUP={_own_group()}\n"
        "chgrp() { return 0; }\n"
        "chmod() { return 0; }\n"
        f"{die_fn}\n{normalise_fn}\n"
        "normalise_release_permissions\n"
        'echo "REACHED END" >&2\n'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode != 0, (
        f"a failing self-check must abort the deploy, not return to the caller; "
        f"stderr={result.stderr!r}"
    )
    assert "other-access denied" in result.stderr
    assert str(corpus) in result.stderr
    assert "REACHED END" not in result.stderr
