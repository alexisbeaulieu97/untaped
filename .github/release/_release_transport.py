"""HTTP and index adapters for the release publication boundary."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from _publication import (
    GitHubRelease,
    ReleaseArtifact,
    ReleaseCandidate,
    _filename_matches_candidate,
)
from _release_core import (
    FULL_SHA_RE,
    PYPI_INDEX,
    SHA256_RE,
    TESTPYPI_INDEX,
    ReleaseCheckError,
    is_prerelease_version,
)


class GitHubReleaseTransport:
    """Small GitHub Releases adapter used only by the workflow boundary."""

    def __init__(
        self,
        *,
        repo: str,
        token: str,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not repo or not token:
            raise ReleaseCheckError("GitHub release transport requires repo and token")
        self.repo = repo
        self.token = token
        self._urlopen = urlopen

    def inspect_github_release(self, *, tag: str) -> GitHubRelease | None:
        data = self._request(
            "GET",
            f"/releases/tags/{urllib.parse.quote(tag, safe='')}",
            missing_ok=True,
        )
        if data is not None:
            release = self._release_from_json(data)
            if release.tag != tag:
                raise ReleaseCheckError("GitHub returned a release with an unexpected tag")
            return release

        matches: list[GitHubRelease] = []
        page = 1
        while True:
            releases = self._request_release_page(page=page)
            for release_data in releases:
                if release_data.get("tag_name") == tag:
                    matches.append(self._release_from_json(release_data))
            if len(releases) < 100:
                break
            page += 1

        if len(matches) > 1:
            raise ReleaseCheckError(f"GitHub returned multiple releases for tag {tag!r}")
        return matches[0] if matches else None

    def _request_release_page(self, *, page: int) -> list[Mapping[str, Any]]:
        data = self._request_url(
            "GET",
            f"https://api.github.com/repos/{self.repo}/releases?per_page=100&page={page}",
        )
        if not isinstance(data, list):
            raise ReleaseCheckError("GitHub returned a non-array release list")
        releases: list[Mapping[str, Any]] = []
        for release in data:
            if not isinstance(release, Mapping):
                raise ReleaseCheckError("GitHub returned a malformed release list")
            releases.append(release)
        return releases

    def inspect_tag_target(self, *, tag: str) -> str | None:
        """Resolve a lightweight or annotated tag to its peeled commit SHA."""
        data = self._request(
            "GET",
            f"/git/ref/tags/{urllib.parse.quote(tag, safe='')}",
            missing_ok=True,
        )
        if data is None:
            return None
        reference = data.get("object")
        if not isinstance(reference, dict):
            raise ReleaseCheckError("GitHub returned a malformed tag reference")
        object_type = reference.get("type")
        object_sha = reference.get("sha")
        if object_type == "tag":
            tag_data = self._request("GET", f"/git/tags/{object_sha}")
            if not isinstance(tag_data, dict):
                raise ReleaseCheckError("GitHub returned a malformed annotated tag")
            reference = tag_data.get("object")
            if not isinstance(reference, dict):
                raise ReleaseCheckError("GitHub returned a malformed annotated tag target")
            object_type = reference.get("type")
            object_sha = reference.get("sha")
        if object_type != "commit" or not isinstance(object_sha, str):
            raise ReleaseCheckError("release tag does not resolve directly to a commit")
        if not FULL_SHA_RE.fullmatch(object_sha):
            raise ReleaseCheckError("release tag target is not a full commit SHA")
        return object_sha

    def create_github_draft(self, candidate: ReleaseCandidate) -> GitHubRelease:
        data = self._request(
            "POST",
            "/releases",
            payload={
                "tag_name": candidate.tag,
                "target_commitish": candidate.candidate_oid,
                "name": f"untaped v{candidate.version}",
                "body": f"PyPI release for {candidate.package_name} {candidate.version}.",
                "draft": True,
                "prerelease": is_prerelease_version(candidate.version),
                "generate_release_notes": False,
            },
        )
        return self._release_from_json(data)

    def inspect_github_assets(self, release: GitHubRelease) -> Mapping[str, str]:
        data = self._request("GET", f"/releases/{release.release_id}")
        assets: dict[str, str] = {}
        for asset in data.get("assets", []):
            if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
                raise ReleaseCheckError("GitHub returned a malformed release asset")
            name = str(asset["name"])
            digest = asset.get("digest")
            if isinstance(digest, str) and digest.startswith("sha256:"):
                digest = digest.removeprefix("sha256:")
            else:
                digest = self._download_digest(str(asset.get("browser_download_url", "")))
            if not SHA256_RE.fullmatch(str(digest)):
                raise ReleaseCheckError(f"GitHub returned no trustworthy digest for {name}")
            assets[name] = str(digest)
        return assets

    def upload_github_asset(self, release: GitHubRelease, artifact: ReleaseArtifact) -> None:
        data = self._request("GET", f"/releases/{release.release_id}")
        upload_url = str(data.get("upload_url", "")).split("{", 1)[0]
        if not upload_url:
            raise ReleaseCheckError("GitHub release did not provide an upload URL")
        url = f"{upload_url}?name={urllib.parse.quote(artifact.filename, safe='')}"
        self._request_url(
            "POST",
            url,
            body=artifact.path.read_bytes(),
            content_type="application/octet-stream",
        )

    def inspect_index(self, *, index: str, candidate: ReleaseCandidate) -> Mapping[str, str] | None:
        return SimpleIndexTransport(urlopen=self._urlopen).inspect_index(
            index=index, candidate=candidate
        )

    def upload_index(self, *, index: str, candidate: ReleaseCandidate) -> None:
        raise ReleaseCheckError(
            f"{index} uploads are performed by the trusted publisher workflow, not this adapter"
        )

    def smoke_published(self, *, index: str, candidate: ReleaseCandidate) -> None:
        raise ReleaseCheckError("published smoke must run in its isolated workflow environment")

    def publish_github_release(self, release: GitHubRelease) -> GitHubRelease:
        data = self._request("PATCH", f"/releases/{release.release_id}", payload={"draft": False})
        return self._release_from_json(data)

    def _release_from_json(self, data: Mapping[str, Any]) -> GitHubRelease:
        release_id = data.get("id")
        tag = data.get("tag_name")
        target_oid = data.get("target_commitish")
        draft = data.get("draft")
        if (
            not isinstance(release_id, (int, str))
            or isinstance(release_id, bool)
            or not isinstance(tag, str)
            or not isinstance(target_oid, str)
            or not isinstance(draft, bool)
        ):
            raise ReleaseCheckError("GitHub returned a malformed release")
        return GitHubRelease(
            release_id=str(release_id),
            tag=tag,
            target_oid=target_oid,
            draft=draft,
            assets={},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        url = f"https://api.github.com/repos/{self.repo}{path}"
        data = self._request_url(method, url, payload=payload, missing_ok=missing_ok)
        if data is not None and not isinstance(data, dict):
            raise ReleaseCheckError("GitHub returned a non-object response")
        return data

    def _request_url(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str = "application/vnd.github+json",
        missing_ok: bool = False,
    ) -> object | None:
        request_body = body
        if payload is not None:
            request_body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            url,
            data=request_body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": content_type,
            },
        )
        try:
            with self._urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise ReleaseCheckError(f"GitHub request failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ReleaseCheckError(f"GitHub request failed: {error}") from error
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseCheckError("GitHub returned an invalid JSON response") from error
        return parsed

    def _download_digest(self, url: str) -> str:
        if not url:
            raise ReleaseCheckError("GitHub asset has no download URL")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._urlopen(request, timeout=30) as response:
                return hashlib.sha256(response.read()).hexdigest()
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            raise ReleaseCheckError(f"could not download GitHub asset digest: {error}") from error


class _SimpleIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


class SimpleIndexTransport:
    """Read-only adapter for exact immutable files from PyPI simple indexes."""

    def __init__(self, *, urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._urlopen = urlopen

    def inspect_index(self, *, index: str, candidate: ReleaseCandidate) -> Mapping[str, str] | None:
        if index not in {"testpypi", "pypi"}:
            raise ReleaseCheckError(f"unknown release index: {index}")
        base = TESTPYPI_INDEX if index == "testpypi" else PYPI_INDEX
        package = urllib.parse.quote(candidate.package_name.lower().replace("_", "-"), safe="")
        url = f"{base}{package}/"
        request = urllib.request.Request(url, headers={"Accept": "text/html"})
        try:
            with self._urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise ReleaseCheckError(
                f"{index} index inspection failed with HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, UnicodeDecodeError) as error:
            raise ReleaseCheckError(f"{index} index inspection failed: {error}") from error

        parser = _SimpleIndexParser()
        parser.feed(text)
        result: dict[str, str] = {}
        for href in parser.links:
            parsed = urllib.parse.urlsplit(href)
            filename = Path(urllib.parse.unquote(parsed.path)).name
            if not _filename_matches_candidate(filename, candidate):
                continue
            digest = urllib.parse.parse_qs(parsed.fragment).get("sha256", [""])[0].lower()
            if not SHA256_RE.fullmatch(digest):
                raise ReleaseCheckError(f"{index} index has no trustworthy digest for {filename}")
            if filename in result:
                raise ReleaseCheckError(f"{index} index repeats candidate file {filename}")
            result[filename] = digest
        return result or None
