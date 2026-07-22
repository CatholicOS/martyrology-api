import base64

import httpx2 as httpx

from .base import ConflictError


class GitHubBackend:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=api_url,
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=httpx.Timeout(30.0),
        )

    def _default_branch(self, repo: str) -> str:
        r = self._client.get(f"/repos/{repo}")
        r.raise_for_status()
        return r.json()["default_branch"]

    def default_branch(self, repo: str) -> str:
        return self._default_branch(repo)

    def ensure_branch(self, repo: str, branch: str) -> None:
        r = self._client.get(f"/repos/{repo}/git/ref/heads/{branch}")
        if r.status_code == 200:
            return
        if r.status_code != 404:
            r.raise_for_status()
        default = self._default_branch(repo)
        base = self._client.get(f"/repos/{repo}/git/ref/heads/{default}")
        base.raise_for_status()
        created = self._client.post(
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base.json()["object"]["sha"]},
        )
        created.raise_for_status()

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None:
        r = self._client.get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        return base64.b64decode(body["content"]), body["sha"]

    def write_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: bytes,
        message: str,
        author_name: str,
        author_email: str,
        expected_sha: str | None = None,
    ) -> str:
        payload = {
            "message": message,
            "branch": branch,
            "content": base64.b64encode(content).decode(),
            "committer": {"name": author_name, "email": author_email},
            "author": {"name": author_name, "email": author_email},
        }
        sha = expected_sha
        if sha is None:
            current = self.read_file(repo, branch, path)
            if current is not None:
                sha = current[1]
        if sha is not None:
            payload["sha"] = sha
        r = self._client.put(f"/repos/{repo}/contents/{path}", json=payload)
        if r.status_code == 409:
            raise ConflictError(f"{path} on {branch}: {r.json().get('message')}")
        if r.status_code == 422:
            message = r.json().get("message") or ""
            if "sha" in message.lower():
                raise ConflictError(f"{path} on {branch}: {message}")
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    def open_pr(self, repo: str, branch: str, title: str) -> str:
        owner = repo.split("/")[0]
        existing = self._client.get(
            f"/repos/{repo}/pulls", params={"head": f"{owner}:{branch}", "state": "open"}
        )
        existing.raise_for_status()
        prs = existing.json()
        if prs:
            return prs[0]["html_url"]
        created = self._client.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": self._default_branch(repo)},
        )
        created.raise_for_status()
        return created.json()["html_url"]
