import subprocess
import tempfile
from pathlib import Path

from .base import ConflictError


class LocalGitBackend:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _bare(self, repo: str) -> Path:
        p = self.root / f"{repo}.git"
        if not p.is_dir():
            raise FileNotFoundError(f"no local repo at {p}")
        return p

    def _git(self, cwd: Path, *args: str, check: bool = True):
        return subprocess.run(["git", *args], cwd=cwd, check=check,
                              capture_output=True, text=False)

    def _default_branch(self, repo: str) -> str:
        out = self._git(self._bare(repo), "symbolic-ref", "HEAD").stdout
        return out.decode().strip().removeprefix("refs/heads/")

    def ensure_branch(self, repo: str, branch: str) -> None:
        bare = self._bare(repo)
        exists = self._git(bare, "show-ref", "--verify", "--quiet",
                           f"refs/heads/{branch}", check=False)
        if exists.returncode != 0:
            self._git(bare, "branch", branch, self._default_branch(repo))

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None:
        bare = self._bare(repo)
        show = self._git(bare, "show", f"{branch}:{path}", check=False)
        if show.returncode != 0:
            return None
        sha = self._git(bare, "rev-parse", f"{branch}:{path}").stdout.decode().strip()
        return show.stdout, sha

    def write_file(self, repo: str, branch: str, path: str, content: bytes,
                   message: str, author_name: str, author_email: str,
                   expected_sha: str | None = None) -> str:
        self.ensure_branch(repo, branch)
        if expected_sha is not None:
            current = self.read_file(repo, branch, path)
            if current is None or current[1] != expected_sha:
                raise ConflictError(f"{path} on {branch} has changed")
        bare = self._bare(repo)
        with tempfile.TemporaryDirectory() as tmp:
            wc = Path(tmp) / "wc"
            self._git(Path(tmp), "clone", "--branch", branch, "--single-branch",
                      str(bare), str(wc))
            target = wc / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self._git(wc, "add", path)
            self._git(wc, "-c", f"user.name={author_name}",
                      "-c", f"user.email={author_email}",
                      "-c", "commit.gpgsign=false",
                      "commit", "-m", message,
                      "--author", f"{author_name} <{author_email}>")
            self._git(wc, "push", "origin", branch)
            return self._git(wc, "rev-parse", "HEAD").stdout.decode().strip()

    def open_pr(self, repo: str, branch: str, title: str) -> str:
        return f"local://{repo}/{branch}"
