"""Minimal GitHub REST client (httpx). Never merges; exposes only the calls the PR executor needs."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

API_VERSION = "2022-11-28"
DEFAULT_BASE_URL = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False, code: str = "github_error"):
        super().__init__(message)
        self.status, self.retryable, self.code = status, retryable, code


@dataclass
class GitHubResponse:
    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def encode_content(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def decode_content(b64: str) -> str:
    return base64.b64decode(b64).decode()


class GitHubClient:
    def __init__(self, *, token: str, owner: str, repo: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0,
                 http_client: httpx.AsyncClient | None = None) -> None:
        self.owner, self.repo, self.base_url, self.timeout = owner, repo, base_url.rstrip("/"), timeout
        self._token = token
        self._http = http_client

    @property
    def repo_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION, "User-Agent": "aeo-sre-executor"}

    async def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> GitHubResponse:
        """Return the response for 2xx/404/409/422 (callers handle them); raise GitHubError otherwise."""
        url = f"{self.base_url}{path}"
        try:
            if self._http is not None:
                resp = await self._http.request(method, url, json=json, params=params, headers=self._headers())
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    resp = await c.request(method, url, json=json, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GitHubError(f"network error calling GitHub: {exc}", retryable=True, code="network_error") from exc
        status = resp.status_code
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        if 200 <= status < 300 or status in (404, 409, 422):
            return GitHubResponse(status, data)
        message = (data or {}).get("message", resp.reason_phrase) if isinstance(data, dict) else resp.reason_phrase
        rate_limited = status == 429 or (status == 403 and (resp.headers.get("x-ratelimit-remaining") == "0"
                                                            or "rate limit" in str(message).lower()))
        if status in (401, 403) and not rate_limited:
            raise GitHubError(f"GitHub rejected credentials/permissions: {message}", status=status, code="auth_error")
        raise GitHubError(f"GitHub {status}: {message}", status=status,
                          retryable=rate_limited or status >= 500, code="rate_limited" if rate_limited else "http_error")

    # ---- thin helpers (path builders shared with dry-run planning) -----------------------------------
    def ref_path(self, branch: str) -> str:
        return f"{self.repo_path}/git/ref/heads/{quote(branch, safe='/')}"

    def contents_path(self, file_path: str) -> str:
        return f"{self.repo_path}/contents/{quote(file_path, safe='/')}"

    async def get_branch_sha(self, branch: str) -> str | None:
        r = await self.request("GET", self.ref_path(branch))
        return r.data["object"]["sha"] if r.status == 200 else None

    async def create_branch(self, branch: str, sha: str) -> GitHubResponse:
        return await self.request("POST", f"{self.repo_path}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    async def delete_branch(self, branch: str) -> GitHubResponse:
        return await self.request("DELETE", f"{self.repo_path}/git/refs/heads/{quote(branch, safe='/')}")

    async def get_file(self, path: str, ref: str) -> tuple[str, str] | None:
        r = await self.request("GET", self.contents_path(path), params={"ref": ref})
        if r.status != 200 or not isinstance(r.data, dict):
            return None
        return decode_content(r.data.get("content", "")), r.data["sha"]

    async def find_pr(self, branch: str) -> dict | None:
        r = await self.request("GET", f"{self.repo_path}/pulls",
                               params={"head": f"{self.owner}:{branch}", "state": "all", "per_page": 10})
        prs = r.data if r.status == 200 and isinstance(r.data, list) else []
        prs = sorted(prs, key=lambda p: (p.get("state") != "open", p.get("number", 0) * -1))
        return prs[0] if prs else None

    async def close_pr(self, number: int) -> GitHubResponse:
        return await self.request("PATCH", f"{self.repo_path}/pulls/{number}", json={"state": "closed"})
