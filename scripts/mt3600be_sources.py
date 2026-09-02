#!/usr/bin/env python3
"""Canonical source-lock validation and change decision for MT3600BE."""

import argparse
import base64
import binascii
import copy
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
from types import MappingProxyType
import urllib.error
import urllib.parse
import urllib.request


_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$", re.IGNORECASE)
_VERSION = re.compile(r"^25\.12\.([0-9]+)$")
_RELEASE_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CANONICAL_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_STAGING_BRANCH = re.compile(r"^auto-update-staging-[1-9][0-9]*-[1-9][0-9]*$")
_IMAGEBUILDER_TAG_PREFIX = "mediatek-filogic-openwrt-"
_SDK_ARCH_TAG_PREFIX = "aarch64_cortex-a53-openwrt-"
_TAG_PAGE_SIZE = 100
_MAX_TAG_PAGES = 100
_PRESENTATION_KEYS = {"display", "description", "label", "url", "name", "title"}
_ALLOWED_HOSTS = {
    "registry-1.docker.io",
    "auth.docker.io",
    "downloads.immortalwrt.org",
    "api.github.com",
    "raw.githubusercontent.com",
}
_FEED_URLS = (
    "https://downloads.immortalwrt.org/releases/{version}/packages/aarch64_cortex-a53/base/Packages.adb",
    "https://downloads.immortalwrt.org/releases/{version}/packages/aarch64_cortex-a53/luci/Packages.adb",
    "https://downloads.immortalwrt.org/releases/{version}/packages/aarch64_cortex-a53/packages/Packages.adb",
)
_NIKKI_REPOSITORY = "nikkinikki-org/OpenWrt-nikki"
_DAEDE_REPOSITORY = "kenzok8/openwrt-daede"
_PACKAGE_ASSIGNMENTS = ("PKG_VERSION", "PKG_RELEASE", "PKG_SOURCE", "PKG_HASH")
_PIN_REPOSITORIES = {
    "DAE": "daeuniverse/dae",
    "DAED": "daeuniverse/daed",
    "DAE_WING": "daeuniverse/dae-wing",
}
_PIN_KEYS = frozenset(
    {"DAE_SOURCE_HASH"}
    | {f"{name}_{suffix}" for name in _PIN_REPOSITORIES for suffix in ("REPOSITORY", "COMMIT")}
)

# Bootstrap-only values from successful GitHub Actions run 33479866793:
# https://github.com/crafoot/CloudImageBuilder/actions/runs/33479866793
_RUN7_COMPATIBILITY = MappingProxyType(
    {
        "IMMORTAL_VERSION": "25.12.1",
        "IMAGEBUILDER_REFERENCE": "immortalwrt/imagebuilder:mediatek-filogic-openwrt-25.12.1",
        "SDK_ARCH_REFERENCE": "immortalwrt/sdk:aarch64_cortex-a53-openwrt-25.12.1@sha256:441f8093008b41301881af4a3ba52e470c3ae579d423445274cf7051048a8eb6",
        "NIKKI_REF": "3799926b147d7065ac98508f16951f8714e53659",
        "DAEDE_REF": "a6c3ced3c7e095630368de96fbf9f2ba03760672",
        "SOURCE_FINGERPRINT": "2fbf063b910436af4c456d5c6677de05d5c480dd83db928247a997e637cd41a3",
    }
)


class Transport:
    """Small, allowlisted JSON/bytes HTTP transport used by source resolution."""

    def __init__(self):
        self._opener = urllib.request.build_opener(_AllowlistedRedirectHandler())

    def _request(self, url: str, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
        _validate_url(url)
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.read(), {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            return error.read(), {key.lower(): value for key, value in error.headers.items()}

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[dict, dict[str, str]]:
        body, response_headers = self._request(url, headers)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON response must be an object")
        return value, response_headers

    def get_bytes(self, url: str) -> bytes:
        return self._request(url)[0]


class FixtureTransport(Transport):
    """Deterministic, literal-response transport for resolver tests and dry runs."""

    def __init__(self, fixture: dict):
        responses = fixture.get("responses") if isinstance(fixture, dict) else None
        if not isinstance(responses, list):
            raise ValueError("fixture must contain a responses list")
        self.responses: dict[str, list[dict]] = {}
        self._positions: dict[str, int] = {}
        for response in responses:
            if not isinstance(response, dict) or not isinstance(response.get("url"), str):
                raise ValueError("fixture response must include a URL")
            _validate_url(response["url"])
            self.responses.setdefault(response["url"], []).append(copy.deepcopy(response))

    @classmethod
    def from_files(cls, *paths):
        transports = [cls(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
        return cls.merge(*transports)

    @classmethod
    def merge(cls, *transports):
        responses = []
        for transport in transports:
            for entries in transport.responses.values():
                responses.extend(copy.deepcopy(entries))
        return cls({"responses": responses})

    def _next(self, url: str) -> dict:
        entries = self.responses.get(url)
        index = self._positions.get(url, 0)
        if not entries or index >= len(entries):
            raise ValueError(f"missing fixture response for {url}")
        self._positions[url] = index + 1
        return entries[index]

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[dict, dict[str, str]]:
        response = self._next(url)
        value = response.get("json")
        if not isinstance(value, dict):
            raise ValueError(f"fixture JSON response missing for {url}")
        response_headers = response.get("headers", {})
        if not isinstance(response_headers, dict):
            raise ValueError(f"fixture response headers must be an object for {url}")
        return copy.deepcopy(value), {str(key).lower(): str(value) for key, value in response_headers.items()}

    def get_bytes(self, url: str) -> bytes:
        response = self._next(url)
        if "bytes_base64" in response:
            value = response["bytes_base64"]
            if not isinstance(value, str) or "bytes" in response:
                raise ValueError(f"fixture base64 bytes response is invalid for {url}")
            try:
                return base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError(f"fixture base64 bytes response is invalid for {url}") from error
        value = response.get("bytes")
        if not isinstance(value, str):
            raise ValueError(f"fixture bytes response missing for {url}")
        return value.encode("utf-8")


def _validate_url(url: str) -> None:
    if not isinstance(url, str):
        raise ValueError("source URL is not allowlisted")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("source URL is not allowlisted") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("source URL host is not allowlisted")


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject unsafe redirects and never forward auth across hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source_host = urllib.parse.urlsplit(req.full_url).hostname
        destination_host = urllib.parse.urlsplit(redirected.full_url).hostname
        if source_host != destination_host:
            for mapping in (redirected.headers, redirected.unredirected_hdrs):
                for header in tuple(mapping):
                    if header.lower() == "authorization":
                        del mapping[header]
        return redirected


def _registry_tags_url(repository: str) -> str:
    return f"https://registry-1.docker.io/v2/{repository}/tags/list?n={_TAG_PAGE_SIZE}"


def _validate_registry_tags_url(url: str, repository: str, *, first_page: bool) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        _validate_url(url)
        if parsed.path != f"/v2/{repository}/tags/list" or parsed.fragment:
            raise ValueError
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        expected = [("n", str(_TAG_PAGE_SIZE))] if first_page else None
        if first_page:
            if pairs != expected:
                raise ValueError
        elif len(pairs) != 2 or {key for key, _ in pairs} != {"n", "last"}:
            raise ValueError
        elif any(not value for _, value in pairs) or dict(pairs)["n"] != str(_TAG_PAGE_SIZE):
            raise ValueError
    except (ValueError, UnicodeError) as error:
        raise ValueError("invalid registry pagination link") from error


def _next_registry_page(link: str, repository: str) -> str:
    match = re.fullmatch(r'\s*<([^<>\s]+)>\s*;\s*rel=(?:"next"|next)\s*', link)
    if not match:
        raise ValueError("invalid registry pagination link")
    url = match.group(1)
    _validate_registry_tags_url(url, repository, first_page=False)
    return url


def _list_registry_tags(transport: Transport, repository: str) -> list[str]:
    url = _registry_tags_url(repository)
    tags = []
    seen = set()
    for _ in range(_MAX_TAG_PAGES):
        _validate_registry_tags_url(url, repository, first_page=not seen)
        if url in seen:
            raise ValueError("invalid registry pagination link: cycle")
        seen.add(url)
        page, headers = transport.get_json(url)
        page_tags = page.get("tags")
        if not isinstance(page_tags, list) or not all(isinstance(tag, str) for tag in page_tags):
            raise ValueError("invalid registry tags response")
        tags.extend(page_tags)
        link = headers.get("link")
        if link is None:
            return tags
        if not isinstance(link, str):
            raise ValueError("invalid registry pagination link")
        url = _next_registry_page(link, repository)
    raise ValueError("invalid registry pagination link: page limit")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _github_json(transport: Transport, path: str, headers: dict[str, str] | None = None) -> dict:
    return transport.get_json(f"https://api.github.com/{path}", headers)[0]


def _github_commit(transport: Transport, repository: str, ref: str, headers=None) -> str:
    value = _github_json(transport, f"repos/{repository}/commits/{ref}", headers)
    return validate_sha(value.get("sha"))


def _github_tree(transport: Transport, repository: str, commit: str, required_paths: tuple[str, ...], headers=None) -> dict:
    tree = _github_json(transport, f"repos/{repository}/git/trees/{commit}?recursive=1", headers).get("tree")
    if not isinstance(tree, list):
        raise ValueError(f"missing package tree for {repository}")
    found = {entry.get("path"): entry for entry in tree if isinstance(entry, dict)}
    for path in required_paths:
        entry = found.get(path)
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            raise ValueError(f"missing package tree path {path}")
        validate_sha(entry.get("sha"))
    return {path: validate_sha(found[path]["sha"]) for path in required_paths}


def _raw_file(transport: Transport, repository: str, commit: str, path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{repository}/{commit}/{path}"
    return transport.get_bytes(url)


def _parse_makefile(raw: bytes, label: str) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} Makefile is not UTF-8") from error
    assignments = {}
    matcher = re.compile(r"^(PKG_VERSION|PKG_RELEASE|PKG_SOURCE|PKG_HASH):=([^\r\n]+)$")
    for line in text.splitlines():
        match = matcher.fullmatch(line)
        if match:
            key, value = match.groups()
            if "$" in value or "`" in value or not value:
                raise ValueError(f"unsafe Makefile assignment {key}")
            if key in assignments:
                raise ValueError(f"duplicate Makefile assignment {key}")
            assignments[key] = value
    if set(assignments) != set(_PACKAGE_ASSIGNMENTS):
        raise ValueError(f"{label} Makefile must contain only required anchored package assignments")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", assignments["PKG_HASH"]):
        raise ValueError(f"{label} PKG_HASH must be SHA-256")
    assignments["PKG_HASH"] = assignments["PKG_HASH"].lower()
    return assignments


def _parse_pins(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("ci/pins.env is not UTF-8") from error
    pins = {}
    matcher = re.compile(r"^([A-Z][A-Z0-9_]*)(?:=)([^\r\n]+)$")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = matcher.fullmatch(line)
        if not match:
            raise ValueError("ci/pins.env has an unanchored assignment")
        key, value = match.groups()
        if key not in _PIN_KEYS or "$" in value or "`" in value or not value:
            raise ValueError("ci/pins.env contains an unsafe or unexpected assignment")
        if key in pins:
            raise ValueError(f"duplicate ci/pins.env assignment {key}")
        pins[key] = value
    if set(pins) != _PIN_KEYS:
        raise ValueError("ci/pins.env is missing required pins")
    for name, repository in _PIN_REPOSITORIES.items():
        if pins[f"{name}_REPOSITORY"] != repository:
            raise ValueError(f"unexpected repository identity for {name}")
        pins[f"{name}_COMMIT"] = validate_sha(pins[f"{name}_COMMIT"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", pins["DAE_SOURCE_HASH"]):
        raise ValueError("DAE_SOURCE_HASH must be SHA-256")
    pins["DAE_SOURCE_HASH"] = pins["DAE_SOURCE_HASH"].lower()
    return pins


def _docker_manifest_digest(transport: Transport, repository: str, tag: str) -> str:
    manifest = f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}"
    _, response_headers = transport.get_json(manifest)
    challenge = response_headers.get("www-authenticate")
    if challenge:
        match = re.fullmatch(r'Bearer realm="([^"]+)",service="([^"]+)",scope="([^"]+)"', challenge)
        if not match:
            raise ValueError("unsupported registry authentication challenge")
        realm, service, scope = match.groups()
        query = urllib.parse.urlencode({"service": service, "scope": scope})
        token_document, _ = transport.get_json(f"{realm}?{query}")
        token = token_document.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("registry token response is invalid")
        _, response_headers = transport.get_json(manifest, {"Authorization": f"Bearer {token}"})
    digest = response_headers.get("docker-content-digest")
    return validate_digest(digest)


def _resolve_registry(transport: Transport) -> tuple[str, dict, dict]:
    try:
        image_tags = _list_registry_tags(transport, "immortalwrt/imagebuilder")
        sdk_tags = _list_registry_tags(transport, "immortalwrt/sdk")
    except (OSError, ValueError) as error:
        raise ValueError("SDK exact version tag is missing") from error
    imagebuilder_versions = [
        tag.removeprefix(_IMAGEBUILDER_TAG_PREFIX)
        for tag in image_tags
        if isinstance(tag, str) and tag.startswith(_IMAGEBUILDER_TAG_PREFIX)
    ]
    sdk_versions = {
        tag.removeprefix(_SDK_ARCH_TAG_PREFIX)
        for tag in sdk_tags
        if isinstance(tag, str) and tag.startswith(_SDK_ARCH_TAG_PREFIX)
    }
    try:
        version = select_stable_25_12([version for version in imagebuilder_versions if version in sdk_versions])
    except ValueError as error:
        raise ValueError("no shared exact target and A53 SDK tag") from error
    imagebuilder_tag = f"{_IMAGEBUILDER_TAG_PREFIX}{version}"
    sdk_tag = f"{_SDK_ARCH_TAG_PREFIX}{version}"
    imagebuilder = {"repository": "immortalwrt/imagebuilder", "tag": imagebuilder_tag, "digest": _docker_manifest_digest(transport, "immortalwrt/imagebuilder", imagebuilder_tag)}
    sdk = {"repository": "immortalwrt/sdk", "tag": sdk_tag, "digest": _docker_manifest_digest(transport, "immortalwrt/sdk", sdk_tag)}
    return version, imagebuilder, sdk


def _resolve_feeds(transport: Transport, version: str) -> dict:
    records = []
    for template in sorted(_FEED_URLS):
        url = template.format(version=version)
        try:
            data = transport.get_bytes(url)
        except (OSError, ValueError) as error:
            raise ValueError(f"missing feed {url}") from error
        records.append({"url": url, "sha256": _sha256(data)})
    records.sort(key=lambda record: record["url"])
    combined = "\n".join(f"{record['url']}\t{record['sha256']}" for record in records).encode("utf-8")
    return {"records": records, "combined_digest": _sha256(combined)}


def _resolve_nikki(transport: Transport, headers=None) -> dict:
    commit = _github_commit(transport, _NIKKI_REPOSITORY, "main", headers)
    paths = ("nikki/Makefile", "luci-app-nikki/Makefile", "mihomo-meta/Makefile")
    tree = _github_tree(transport, _NIKKI_REPOSITORY, commit, paths, headers)
    packages = {}
    for path in paths:
        package = _parse_makefile(_raw_file(transport, _NIKKI_REPOSITORY, commit, path), path)
        package["tree_sha"] = tree[path]
        packages[path.rsplit("/", 1)[0]] = package
    return {"repository": _NIKKI_REPOSITORY, "commit": commit, "packages": packages}


def _resolve_daede(transport: Transport, headers=None) -> dict:
    commit = _github_commit(transport, _DAEDE_REPOSITORY, "main", headers)
    paths = ("dae/Makefile", "daed/Makefile", "luci-app-daede/Makefile", "ci/pins.env")
    tree = _github_tree(transport, _DAEDE_REPOSITORY, commit, paths, headers)
    packages = {}
    for path in paths[:3]:
        package = _parse_makefile(_raw_file(transport, _DAEDE_REPOSITORY, commit, path), path)
        package["tree_sha"] = tree[path]
        packages[path.rsplit("/", 1)[0]] = package
    pins = _parse_pins(_raw_file(transport, _DAEDE_REPOSITORY, commit, "ci/pins.env"))
    not_ready = []
    for name, repository in _PIN_REPOSITORIES.items():
        pinned = pins[f"{name}_COMMIT"]
        head = _github_commit(transport, repository, "main", headers)
        if head != pinned:
            comparison = _github_json(transport, f"repos/{repository}/compare/{pinned}...{head}", headers)
            if comparison.get("status") == "ahead":
                not_ready.append(name.lower().replace("_", "-"))
    dae_pin = {key: pins[key] for key in ("DAE_REPOSITORY", "DAE_COMMIT", "DAE_SOURCE_HASH")}
    dae_pin["tree_sha"] = tree["ci/pins.env"]
    daed_pin = {key: pins[key] for key in ("DAED_REPOSITORY", "DAED_COMMIT", "DAE_WING_REPOSITORY", "DAE_WING_COMMIT")}
    return {
        "repository": _DAEDE_REPOSITORY,
        "commit": commit,
        "dae": {"package": packages["dae"], "pins": dae_pin},
        "daed": {"package": packages["daed"], "pins": daed_pin},
        "luci": packages["luci-app-daede"],
    }, not_ready


def resolve_candidate(transport: Transport, previous: dict | None) -> dict:
    """Resolve every build input into a deterministic candidate lock.

    ``previous`` is accepted so callers can use the same interface for decision
    making; the candidate itself contains no previous-lock state.
    """
    del previous
    version, imagebuilder, sdk = _resolve_registry(transport)
    github_headers = getattr(transport, "github_headers", None)
    immortal_commit = _github_commit(transport, "immortalwrt/immortalwrt", f"immortalwrt-{version}", github_headers)
    daede, not_ready = _resolve_daede(transport, github_headers)
    candidate = {
        "schema": 1,
        "immortalwrt": {
            "version": version,
            "commit": immortal_commit,
            "imagebuilder": imagebuilder,
            "sdk": sdk,
            "feeds": _resolve_feeds(transport, version),
        },
        "nikki": _resolve_nikki(transport, github_headers),
        "daede": daede,
    }
    if not_ready:
        candidate["resolution"] = "not-ready"
        candidate["not_ready_repositories"] = not_ready
    else:
        candidate["resolution"] = "ready"
    return candidate


def validate_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("Git SHA must be a 40-character hexadecimal value")
    return value.lower()


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("container digest must use sha256 and 64 hexadecimal characters")
    return value.lower()


def automatic_tag(version: str, release_date: str, source_fingerprint: str) -> str:
    """Build the stable automatic MT3600BE release identity."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("automatic release version must be stable 25.12.x")
    if not isinstance(release_date, str) or not _RELEASE_DATE.fullmatch(release_date):
        raise ValueError("automatic release date must use YYYY-MM-DD")
    try:
        parsed_date = datetime.date.fromisoformat(release_date)
    except ValueError as error:
        raise ValueError("automatic release date must be a valid YYYY-MM-DD date") from error
    if parsed_date.isoformat() != release_date:
        raise ValueError("automatic release date must use YYYY-MM-DD")
    if not isinstance(source_fingerprint, str) or not _CANONICAL_FINGERPRINT.fullmatch(source_fingerprint):
        raise ValueError("automatic release fingerprint must be a lowercase SHA-256")
    return f"glinet_gl-mt3600be-{version}-auto-{release_date.replace('-', '')}-{source_fingerprint[:12]}"


def validate_staging_branch(value: str) -> str:
    """Accept only the exact branch namespace reserved for updater runs."""
    if not isinstance(value, str) or not _STAGING_BRANCH.fullmatch(value):
        raise ValueError("invalid automatic staging branch")
    return value


def staging_branch(run_id: int, attempt: int) -> str:
    """Build a current-run-only staging branch from GitHub run identity."""
    for label, value in (("run ID", run_id), ("run attempt", attempt)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{label} must be a positive integer")
    return validate_staging_branch(f"auto-update-staging-{run_id}-{attempt}")


def _validate_hash(value: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("SHA-256 hash must be 64 hexadecimal characters")
    return value.lower()


def select_stable_25_12(tags: list[str]) -> str:
    eligible = []
    for tag in tags:
        match = _VERSION.fullmatch(tag) if isinstance(tag, str) else None
        if match:
            eligible.append((int(match.group(1)), tag))
    if not eligible:
        raise ValueError("no stable 25.12.x version found")
    return max(eligible)[1]


def _without_presentation(value):
    if isinstance(value, dict):
        normalized = {}
        for key, child in value.items():
            if key in _PRESENTATION_KEYS:
                continue
            if _is_git_reference_key(key):
                child = validate_sha(child)
            elif key == "sha256":
                child = _validate_hash(child)
            elif key == "digest":
                child = validate_digest(child)
            normalized[key] = _without_presentation(child)
        return normalized
    if isinstance(value, list):
        return [_without_presentation(item) for item in value]
    return value


def canonical_bytes(state: dict) -> bytes:
    canonical = _without_presentation(copy.deepcopy(state))
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fingerprint(state: dict) -> str:
    return hashlib.sha256(canonical_bytes(state)).hexdigest()


def _group_value(state, path):
    value = state
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _canonical_group_value(value, path):
    # Keep scalar references in the same key context as their source lock.
    if value is None:
        return canonical_bytes(None)
    if path[-1] in {"commit", "sha", "sha1", "digest"}:
        return canonical_bytes({path[-1]: value})
    return canonical_bytes(value)


_GROUPS = {
    "immortalwrt.commit": ("immortalwrt", "commit"),
    "immortalwrt.imagebuilder": ("immortalwrt", "imagebuilder"),
    "immortalwrt.sdk": ("immortalwrt", "sdk"),
    "immortalwrt.feeds": ("immortalwrt", "feeds"),
    "nikki": ("nikki",),
    "daede.dae": ("daede", "dae"),
    "daede.daed": ("daede", "daed"),
    "daede.luci": ("daede", "luci"),
}


def _validate_known_references(value, path="state"):
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_git_reference_key(key):
                validate_sha(child)
            elif key == "sha256":
                _validate_hash(child)
            elif key == "digest":
                validate_digest(child)
            else:
                _validate_known_references(child, f"{path}.{key}")
    elif isinstance(value, list):
        for child in value:
            _validate_known_references(child, path)


def _is_git_reference_key(key: str) -> bool:
    return key in {"commit", "sha", "sha1", "tree_sha"} or key.endswith("_COMMIT")


def compare_states(previous: dict | None, candidate: dict) -> tuple[str, list[str]]:
    if not isinstance(candidate, dict):
        raise ValueError("candidate lock must be an object")
    immortalwrt = candidate.get("immortalwrt")
    if not isinstance(immortalwrt, dict) or not isinstance(immortalwrt.get("version"), str):
        return "not-ready", ["immortalwrt"]
    if not _VERSION.fullmatch(immortalwrt["version"]):
        return "not-ready", ["immortalwrt"]
    _validate_known_references(candidate)
    if previous is None:
        return "changed", ["bootstrap"]
    if not isinstance(previous, dict):
        raise ValueError("previous lock must be an object")
    _validate_known_references(previous)
    changed = []
    for name, path in _GROUPS.items():
        if _canonical_group_value(_group_value(previous, path), path) != _canonical_group_value(_group_value(candidate, path), path):
            changed.append(name)
    if _canonical_group_value(_group_value(previous, ("immortalwrt", "version")), ("version",)) != canonical_bytes(immortalwrt["version"]):
        changed.insert(0, "immortalwrt.version")
    return ("changed" if changed else "unchanged"), changed


def _cli_compare(args):
    try:
        candidate = json.loads(open(args.candidate, encoding="utf-8").read())
        previous = None if args.previous == "-" else json.loads(open(args.previous, encoding="utf-8").read())
        decision, groups = compare_states(previous, candidate)
        output = {"decision": decision, "fingerprint": fingerprint(candidate), "changed_groups": groups}
        print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return {"unchanged": 0, "changed": 0, "not-ready": 2}[decision]
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(json.dumps({"decision": "invalid", "fingerprint": None, "changed_groups": [], "error": str(error)}, sort_keys=True, separators=(",", ":")))
        return 3


def _read_json(path: str) -> dict:
    value = json.loads(open(path, encoding="utf-8").read())
    if not isinstance(value, dict):
        raise ValueError("lock must be a JSON object")
    return value


def _write_json_atomically(path: str, value: dict) -> None:
    destination = os.path.abspath(path)
    directory = os.path.dirname(destination)
    if not os.path.isdir(directory):
        raise ValueError("output directory does not exist")
    descriptor, temporary = tempfile.mkstemp(prefix=".mt3600be-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _cli_resolve(args):
    try:
        previous = None if args.previous == "-" else _read_json(args.previous)
        if args.fixture:
            fixture_path = os.path.abspath(args.fixture)
            fixture_directory = os.path.dirname(fixture_path)
            fixture_paths = sorted(
                os.path.join(fixture_directory, name)
                for name in os.listdir(fixture_directory)
                if name.endswith(".json")
                and not name.startswith("previous-")
                and not name.startswith("candidate-")
                and "not-ready" not in name
            )
            if fixture_path not in fixture_paths:
                fixture_paths.append(fixture_path)
            transport = FixtureTransport.from_files(*(pathlib.Path(path) for path in fixture_paths))
        else:
            transport = Transport()
        token = os.environ.get(args.github_token_env)
        if token:
            transport.github_headers = {"Authorization": f"Bearer {token}"}
        candidate = resolve_candidate(transport, previous)
        if candidate.get("resolution") == "not-ready":
            decision, groups = "not-ready", candidate["not_ready_repositories"]
        else:
            decision, groups = compare_states(previous, candidate)
        _write_json_atomically(args.output, candidate)
        print(json.dumps({"decision": decision, "fingerprint": fingerprint(candidate), "changed_groups": groups}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return {"unchanged": 0, "changed": 0, "not-ready": 2}[decision]
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(json.dumps({"decision": "invalid", "fingerprint": None, "changed_groups": [], "error": str(error)}, sort_keys=True, separators=(",", ":")))
        return 3


def _cli_build_env(args):
    try:
        if not os.path.lexists(args.lock):
            if not args.compat_run7:
                raise ValueError("candidate lock is missing")
            print("\n".join(f"{key}={value}" for key, value in _RUN7_COMPATIBILITY.items()))
            return 0
        lock = _read_json(args.lock)
        if lock.get("resolution") == "not-ready":
            raise ValueError("cannot build a not-ready candidate")
        immortalwrt = lock.get("immortalwrt")
        if not isinstance(immortalwrt, dict):
            raise ValueError("missing immortalwrt lock")
        version = immortalwrt.get("version")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("invalid ImmortalWrt version")
        refs = {}
        for name, key, repository, tag in (
            ("IMAGEBUILDER_REFERENCE", "imagebuilder", "immortalwrt/imagebuilder", f"{_IMAGEBUILDER_TAG_PREFIX}{version}"),
            ("SDK_ARCH_REFERENCE", "sdk", "immortalwrt/sdk", f"{_SDK_ARCH_TAG_PREFIX}{version}"),
        ):
            image = immortalwrt.get(key)
            if not isinstance(image, dict) or image.get("tag") != tag or image.get("repository") != repository:
                raise ValueError(f"invalid {key} reference")
            refs[name] = f"{repository}:{tag}@{validate_digest(image.get('digest'))}"
        nikki = lock.get("nikki")
        daede = lock.get("daede")
        if not isinstance(nikki, dict) or not isinstance(daede, dict):
            raise ValueError("missing proxy package lock")
        values = {
            "IMMORTAL_VERSION": version,
            "IMAGEBUILDER_REFERENCE": refs["IMAGEBUILDER_REFERENCE"],
            "SDK_ARCH_REFERENCE": refs["SDK_ARCH_REFERENCE"],
            "NIKKI_REF": validate_sha(nikki.get("commit")),
            "DAEDE_REF": validate_sha(daede.get("commit")),
            "SOURCE_FINGERPRINT": fingerprint(lock),
        }
        print("\n".join(f"{key}={value}" for key, value in values.items()))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True, separators=(",", ":")))
        return 3


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--previous", required=True)
    compare.add_argument("--candidate", required=True)
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--previous", required=True)
    resolve.add_argument("--output", required=True)
    resolve.add_argument("--github-token-env", default="GH_TOKEN")
    resolve.add_argument("--fixture")
    build_env = subparsers.add_parser("build-env")
    build_env.add_argument("--lock", required=True)
    build_env.add_argument("--compat-run7", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return _cli_compare(args)
    if args.command == "resolve":
        return _cli_resolve(args)
    if args.command == "build-env":
        return _cli_build_env(args)
    return 3


if __name__ == "__main__":
    sys.exit(main())
