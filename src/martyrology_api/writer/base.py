from typing import Protocol


class ConflictError(Exception):
    pass


class VcsBackend(Protocol):
    def default_branch(self, repo: str) -> str: ...

    def ensure_branch(self, repo: str, branch: str) -> None: ...

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None: ...

    def write_file(self, repo: str, branch: str, path: str, content: bytes,
                   message: str, author_name: str, author_email: str,
                   expected_sha: str | None = None) -> str: ...

    def open_pr(self, repo: str, branch: str, title: str) -> str: ...
