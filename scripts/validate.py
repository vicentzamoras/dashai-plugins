"""The store's bot, without dashAI: checks what can be checked from GitHub and
the plugin's files alone, and builds index.json.

    python scripts/validate.py check <plugins/x.json>...   # a PR (PR_AUTHOR)
    python scripts/validate.py build [--full]              # index.json

Checked here: the entry, the repo (public, not archived, license), its
releases (one .zip/.tar.gz or the tag's source code, at most 200 MB, the
attestation with `gh attestation verify`) and the manifest (fields, id, the
version equals the tag, component types, each class exists, read with ast;
the plugins it needs have an entry here).
A PR's component names must not be another store plugin's nor dashAI's own
(its `get_initial_components`, read with ast from GitHub). Not checked: what
needs dashAI running (pip resolving the dependencies with its own, loading the
code). dashAI checks all of it again when installing: this only decides what
the store lists.

The build reuses a release already in index.json while its file (or, for
source code, its commit) is the same: it only reviews what is new. It reads
every repo's state and releases with GraphQL, 100 repos per query (``triage``),
so a plugin that didn't change costs no REST request; a release that was left
out and is older than a listed one is not reviewed again. `--full` reviews
everything again (when the rules change). index.json only changes (and
``built_at`` with it) when what it lists changes, so dashAI's ETag holds
between builds. When a new release (or the whole
plugin) is left out, the build tells its author in an issue of this repo
(``notify``); outside GitHub Actions it only prints what it would do.

Standard library and the `gh` CLI only; GITHUB_TOKEN raises GitHub's limit.
"""

import ast
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com/"
TOKEN = os.environ.get("GITHUB_TOKEN")
MAX_BYTES = 200 * 1024 * 1024  # dashAI's cap (sources/base.check_size)
KEEP = 5  # releases per plugin in the index
ID = re.compile(r"^(?!(con|prn|aux|nul|com\d|lpt\d)$)[a-z0-9]+(-[a-z0-9]+)*$")
REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")
CLASS_REF = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
VERSION = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")
TYPES = {
    "Model", "Converter", "Metric", "Task", "DataLoader", "Explorer",
    "GlobalExplainer", "LocalExplainer", "GenerativeModel",
    "GenerativeTask",
}  # dashAI's ALLOWED_COMPONENT_TYPES (plugins/validator.py)
LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-3.0",
    "LGPL-3.0", "Unlicense",
}
ROOT = Path(".")
# dashAI's own components, released (production) and upcoming (develop).
DASHAI_COMPONENTS = (
    "https://raw.githubusercontent.com/DashAISoftware/DashAI/{}/DashAI/back/initial_components.py"
)
DASHAI_BRANCHES = ("production", "develop")


class Invalid(Exception):
    """What the store refuses; its message says why."""


def get(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dashai-plugins"}
    if TOKEN and url.startswith(API):  # the token only goes to the API
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as r:
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise Invalid(f"not found: {url}") from error
        raise
    if len(data) > MAX_BYTES:
        raise Invalid(f"larger than 200 MB: {url}")
    return data


def api(path):
    data = get(API + path)
    return json.loads(data) if data else None


# What ``triage`` asks of each repo. 5 releases (a query of 100 repos costs 6
# of the 1,000 points an hour GITHUB_TOKEN has: 10,000 plugins take 600).
GRAPH_REPO = """nameWithOwner isPrivate isArchived licenseInfo { spdxId }
releases(first: 5, orderBy: {field: CREATED_AT, direction: DESC}) { nodes {
  tagName isDraft isPrerelease publishedAt tagCommit { oid }
  releaseAssets(first: 20) { nodes { name size digest downloadUrl } } } }"""


def triage(repos):
    """lowercased repo -> (its state, its releases) as the REST API gives
    them (``check_repo``, ``releases``), 100 repos per GraphQL query. Missing:
    what GitHub didn't answer (not found, rate limit), checked with REST then.
    Releases are None when a prerelease may hide a release REST would list."""
    if not TOKEN:  # GraphQL needs one
        return {}
    found = {}
    for start in range(0, len(repos), 100):
        chunk = repos[start:start + 100]
        query = " ".join(
            f'r{i}: repository(owner: "{repo.split("/")[0]}", name: "{repo.split("/")[1]}") {{ {GRAPH_REPO} }}'
            for i, repo in enumerate(chunk)
        )
        request = urllib.request.Request(
            API + "graphql", data=json.dumps({"query": "{ " + query + " }"}).encode(),
            headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "dashai-plugins"},
        )
        with urllib.request.urlopen(request) as r:
            data = json.loads(r.read()).get("data") or {}
        for i, repo in enumerate(chunk):
            node = data.get(f"r{i}")
            if not node:
                continue
            state = {
                "full_name": node["nameWithOwner"],
                "private": node["isPrivate"],
                "archived": node["isArchived"],
                "license": {"spdx_id": (node["licenseInfo"] or {}).get("spdxId")},
            }
            nodes = node["releases"]["nodes"]
            listed = [
                {
                    "tag_name": n["tagName"],
                    "draft": n["isDraft"],
                    "prerelease": n["isPrerelease"],
                    "published_at": n["publishedAt"],
                    "tag_commit": (n["tagCommit"] or {}).get("oid"),
                    "assets": [
                        {"name": a["name"], "size": a["size"], "digest": a["digest"],
                         "browser_download_url": a["downloadUrl"]}
                        for a in n["releaseAssets"]["nodes"]
                    ],
                }
                for n in nodes
            ]
            hidden = len(nodes) == 5 and any(r["draft"] or r["prerelease"] for r in listed)
            found[repo.lower()] = (state, None if hidden else listed)
    return found


def load_entry(path, removed):
    """The entry's own rules (shape, name, id); returns it."""
    path = Path(path)
    try:
        entry = json.loads(path.read_text("utf-8"))
    except ValueError as error:
        raise Invalid(f"{path.name} is not JSON: {error}") from error
    if not isinstance(entry, dict) or set(entry) != {"id", "repo"}:
        raise Invalid(f'{path.name} must have exactly "id" and "repo"')
    plugin_id, repo = entry["id"], entry["repo"]
    if path.name != f"{plugin_id}.json":
        raise Invalid(f'the file must be named after its id: plugins/{plugin_id}.json')
    if not isinstance(plugin_id, str) or not ID.match(plugin_id):
        raise Invalid(f"invalid id {plugin_id!r}: lowercase letters and digits, joined by single -")
    if "dashai" in plugin_id:
        raise Invalid('the id cannot contain "dashai"')
    if plugin_id in removed:
        raise Invalid(f"the id {plugin_id!r} belongs to a removed plugin")
    if not isinstance(repo, str) or not REPO.match(repo):
        raise Invalid(f'repo must be "owner/name", not {repo!r}')
    return entry


def check_repo(repo, data=None):
    """Refuses a repo the store can't list; returns its license (SPDX id).
    ``data``: its state, if ``triage`` already read it."""
    try:
        data = data or api(f"repos/{repo}")
    except Invalid as error:
        raise Invalid(f"{repo} does not exist or is private") from error
    if data["full_name"].lower() != repo.lower():  # renamed or moved
        raise Invalid(f"{repo} is now {data['full_name']}: update the entry")
    if data["private"]:
        raise Invalid(f"{repo} is private")
    if data["archived"]:
        raise Invalid(f"{repo} is archived")
    spdx = (data.get("license") or {}).get("spdx_id")
    if spdx not in LICENSES:
        raise Invalid(f"{repo} has no allowed license (found {spdx}); allowed: {', '.join(sorted(LICENSES))}")
    return spdx


def releases(repo, found=None):
    """The newest published releases. ``found``: as ``triage`` read them."""
    found = api(f"repos/{repo}/releases?per_page=20") if found is None else found
    found = [r for r in found if not r["draft"] and not r["prerelease"]]
    if not found:
        raise Invalid(f"{repo} has no published release")
    return found[:KEEP]


def extract(blob, name, into):
    """A .zip or .tar.gz into ``into``; returns the folder with manifest.json
    (the archive's root or its only folder, as dashAI looks for it)."""
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                if sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
                    raise Invalid(f"{name}: larger than 200 MB extracted")
                archive.extractall(into)  # drops absolute and .. paths
        else:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
                if sum(m.size for m in archive.getmembers()) > MAX_BYTES:
                    raise Invalid(f"{name}: larger than 200 MB extracted")
                archive.extractall(into, filter="data")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as error:
        raise Invalid(f"{name} cannot be extracted: {error}") from error
    if (into / "manifest.json").is_file():
        return into
    folders = [p for p in into.iterdir() if p.is_dir() and not p.name.startswith((".", "__MACOSX"))]
    if len(folders) == 1 and (folders[0] / "manifest.json").is_file():
        return folders[0]
    raise Invalid(f"{name} has no manifest.json at its root")


def release_file(release):
    """The release's single .zip/.tar.gz, or None: dashAI then installs the
    tag's source code."""
    assets = [a for a in release["assets"] if a["name"].lower().endswith((".zip", ".tar.gz"))]
    return assets[0] if len(assets) == 1 else None


def tag_commit(repo, release):
    """The commit of the release's tag (``triage`` already read it)."""
    if release.get("tag_commit"):
        return release["tag_commit"]
    tag = release["tag_name"]
    return api(f"repos/{repo}/commits/{urllib.parse.quote(tag, safe='')}")["sha"]


def utc(timestamp):
    return datetime.fromisoformat(timestamp).astimezone(timezone.utc).isoformat(timespec="seconds")


def attestation(file, repo, tag):
    """What a valid attestation of ``file`` records (the commit the build ran
    on, its workflow and when it was signed), or None if it has none. `gh`
    checks the same as dashAI: this repo's GitHub Actions, at this tag."""
    run = subprocess.run(
        ["gh", "attestation", "verify", str(file), "--repo", repo,
         "--source-ref", f"refs/tags/{tag}", "--format", "json"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        return None
    details = {"commit": None, "workflow": None, "signed_at": None}
    try:  # verified: a detail it can't read is only left out
        result = json.loads(run.stdout)[0]["verificationResult"]
        build = result["statement"]["predicate"]["buildDefinition"]
        details["workflow"] = build["externalParameters"]["workflow"]["path"]
        details["commit"] = next(
            (d["digest"]["gitCommit"] for d in build.get("resolvedDependencies", [])
             if "gitCommit" in d.get("digest", {})), None)
        details["signed_at"] = utc(result["verifiedTimestamps"][0]["timestamp"])
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return details


def fetch(repo, release, into):
    """What dashAI would download for this release: its folder, and how the
    index lists it (sha256, trust level, commit; an attestation's workflow and
    signing time). The sha256 is computed as dashAI does, so installing can
    compare it."""
    tag = release["tag_name"]
    asset = release_file(release)
    if asset:
        if asset["size"] > MAX_BYTES:
            raise Invalid(f"{asset['name']} is larger than 200 MB")
        blob = get(asset["browser_download_url"])
        sha256 = hashlib.sha256(blob).hexdigest()
        if asset.get("digest") and asset["digest"] != f"sha256:{sha256}":
            raise Invalid(f"{asset['name']} does not match GitHub's digest")
        file = into / asset["name"]
        file.write_bytes(blob)
        attested = attestation(file, repo, tag)
        listed = {"sha256": sha256, "trust_level": "github_asset"}
        if attested:
            listed.update(trust_level="github_attested", workflow=attested["workflow"],
                          signed_at=attested["signed_at"])
        listed["commit"] = (attested or {}).get("commit") or tag_commit(repo, release)
        return extract(blob, asset["name"].lower(), into / "plugin"), listed
    # No single file: the source code at the tag's commit (a tag can move).
    commit = tag_commit(repo, release)
    blob = get(f"https://codeload.github.com/{repo}/tar.gz/{commit}")
    sha256 = hashlib.sha256(gzip.decompress(blob)).hexdigest()  # the tar's, as dashAI
    listed = {"sha256": sha256, "trust_level": "github_source", "commit": commit}
    return extract(blob, "source.tar.gz", into / "plugin"), listed


def has_readme(repo, commit):
    """dashAI shows a store plugin's README from its repo at the reviewed
    commit: README.md at the root, with this exact name (raw is case
    sensitive)."""
    try:
        get(f"https://raw.githubusercontent.com/{repo}/{commit}/README.md")
    except Invalid:
        return False
    return True


def unchanged(repo, release, old):
    """Whether ``old`` (this tag in the last index) still describes the
    release: the same file (GitHub's digest) or, for source code, the same
    commit."""
    if not old or "commit" not in old:
        return False
    asset = release_file(release)
    if asset:
        return old["trust_level"] != "github_source" and asset.get("digest") == f"sha256:{old['sha256']}"
    return old["trust_level"] == "github_source" and tag_commit(repo, release) == old["commit"]


def defines(folder, class_ref):
    """Whether ``module:Class`` names a class defined in the plugin's files."""
    module, name = class_ref.split(":")
    root = folder / "src" if (folder / "src").is_dir() else folder  # as dashAI imports
    base = root.joinpath(*module.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            try:
                tree = ast.parse(path.read_bytes())
            except SyntaxError as error:
                raise Invalid(f"{path.name} has a syntax error: {error}") from error
            return any(isinstance(n, ast.ClassDef) and n.name == name for n in tree.body)
    return False


def http_link(value):
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def check_manifest(folder, plugin_id, tag):
    try:
        manifest = json.loads((folder / "manifest.json").read_text("utf-8-sig"))
    except ValueError as error:
        raise Invalid(f"manifest.json is not UTF-8 JSON: {error}") from error
    if manifest.get("manifest_version") != "1":
        raise Invalid('manifest_version must be "1"')
    if manifest.get("id") != plugin_id:
        raise Invalid(f"the manifest's id is {manifest.get('id')!r}, the entry's {plugin_id!r}")
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION.match(version):
        raise Invalid(f"invalid version {version!r}")
    if version != tag.removeprefix("v"):
        raise Invalid(f"version {version} does not match the tag {tag}")
    if not isinstance(manifest.get("min_app_version"), str) or not VERSION.match(manifest["min_app_version"]):
        raise Invalid("min_app_version must be a version, e.g. 0.10.0")
    if not str(manifest.get("name") or "").strip():
        raise Invalid("the manifest needs a name")
    authors = manifest.get("authors")
    if not isinstance(authors, list) or not authors:
        raise Invalid("the manifest needs at least one author")
    links = [a.get("url") for a in authors if isinstance(a, dict) and a.get("url")]
    links += list((manifest.get("project_urls") or {}).values())
    if not all(http_link(link) for link in links):
        raise Invalid("every link must be http(s)")
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise Invalid("the manifest needs at least one component")
    for component in components:
        ref, kind = component.get("class", ""), component.get("type")
        if not CLASS_REF.match(ref):
            raise Invalid(f"class must be 'module:Class', not {ref!r}")
        if kind not in TYPES:
            raise Invalid(f"{ref}: type {kind!r} is not one dashAI accepts")
        if not defines(folder, ref):
            raise Invalid(f"{ref}: no such class in the plugin")
    names = [c["class"].split(":")[1] for c in components]
    if len(set(names)) != len(names):
        raise Invalid("component class names must be unique")
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        raise Invalid("dependencies must be a list of pip requirements")
    needs = manifest.get("plugin_dependencies", [])
    if not isinstance(needs, list) or not all(isinstance(i, str) and ID.match(i) for i in needs):
        raise Invalid("plugin_dependencies must be a list of plugin ids")
    if len(set(needs)) != len(needs) or plugin_id in needs:
        raise Invalid("plugin_dependencies can't repeat an id nor name the plugin itself")
    return manifest


def plugin_fields(manifest):
    """What the index lists about the plugin itself, from its newest release."""
    components = manifest["components"]
    return {
        "name": manifest["name"],
        "description": manifest.get("description", ""),
        "authors": [
            {key: str(author.get(key, "")) for key in ("name", "email", "url")}
            for author in manifest["authors"]
        ],
        "components": [c["class"].split(":")[1] for c in components],
        "component_types": [c["type"] for c in components],
        "project_urls": manifest.get("project_urls", {}),
    }


PLUGIN_FIELDS = ("name", "description", "authors", "components", "component_types", "project_urls")


def review(repo, release, plugin_id):
    """(how the index lists the release, the plugin's fields from it)."""
    tag = release["tag_name"]
    with tempfile.TemporaryDirectory() as tmp:
        folder, listed = fetch(repo, release, Path(tmp))
        manifest = check_manifest(folder, plugin_id, tag)
    if not has_readme(repo, listed["commit"]):
        raise Invalid(f"no README.md at the repo's root in {listed['commit'][:7]}")
    listed = {
        "tag": tag,
        "version": manifest["version"],
        "min_app_version": manifest["min_app_version"],
        **listed,
        "published_at": release["published_at"],
        "dependencies": manifest.get("dependencies", []),
        "plugin_dependencies": manifest.get("plugin_dependencies", []),
    }
    return {k: v for k, v in listed.items() if v is not None}, plugin_fields(manifest)


def taken_names(plugins, plugin_id, dashai):
    """Component name -> who has it: dashAI, or another plugin of the store."""
    taken = {name: p["id"] for p in plugins if p["id"] != plugin_id for name in p["components"]}
    return {**taken, **{name: "dashAI" for name in dashai}}


def check_names(components, taken):
    clash = [name for name in components if name in taken]
    if clash:
        raise Invalid(
            f"component names already used: {', '.join(f'{n} ({taken[n]})' for n in clash)}; "
            "rename these classes with a prefix of your own and publish a new release"
        )


def check_needs(needs, ids):
    """dashAI only finds a plugin it needs in the store (never a repo the
    manifest names), so each must have an entry."""
    unknown = [i for i in needs if i not in ids]
    if unknown:
        raise Invalid(f"plugin_dependencies not in the store: {', '.join(unknown)}; add them first")


def describe(entry, log, known=None, taken=None, listed_before=(), left_out=None, state=None, ids=frozenset()):
    """The entry as index.json lists it; None if no release passes. ``known``:
    its entry in the last index, whose unchanged releases are reused, and a
    release it left out that is older than one that passes is not reviewed
    again. ``log`` gets one line per release. ``taken``: component names a new
    release can't use (``taken_names``); the tags in ``listed_before`` are not
    checked against them, so dashAI adding a name later doesn't drop what is
    listed. ``left_out`` gets why each release newer than the newest that
    passes fails. ``state``: the repo and its releases as ``triage`` read them.
    ``ids``: the store's entries, the only plugins a release may need."""
    repo = entry["repo"]
    repo_data, found = state or (None, None)
    license_id = check_repo(repo, repo_data)
    location = f"https://github.com/{repo}"
    if known and known.get("location") != location:
        known = None  # another repo now: nothing to reuse
    old = {r["tag"]: r for r in (known or {}).get("releases", [])}
    passed = []  # (listed release, the plugin's fields from it; None if reused)
    for release in releases(repo, found):
        tag = release["tag_name"]
        if unchanged(repo, release, old.get(tag)):
            log(f"  {tag}: ok, unchanged ({old[tag]['trust_level']})")
            passed.append((old[tag], None))
            continue
        if known and tag not in old and passed:
            log(f"  {tag}: left out before, not reviewed again")
            continue
        try:
            listed, fields = review(repo, release, entry["id"])
            check_needs(listed["plugin_dependencies"], ids)
            if tag not in listed_before:
                check_names(fields["components"], taken or {})
        except Invalid as error:
            log(f"  {tag}: {error}")
            if left_out is not None and not passed:
                left_out.append(f"{tag}: {error}")
            continue
        log(f"  {tag}: ok ({listed['trust_level']})")
        passed.append((listed, fields))
    if not passed:
        return None
    # The newest release's fields; reused, it was the newest one before too.
    fields = passed[0][1] or {key: known[key] for key in PLUGIN_FIELDS}
    return {
        "id": entry["id"],
        "source": "git",
        "location": location,
        **fields,
        "license": license_id,
        "releases": [listed for listed, _ in passed],
    }


def dashai_components():
    """The class names dashAI registers itself: the ones its
    ``get_initial_components`` lists. A plugin can't take them."""
    names = set()
    for branch in DASHAI_BRANCHES:
        tree = ast.parse(get(DASHAI_COMPONENTS.format(branch)))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_initial_components":
                names |= {
                    item.id
                    for found in ast.walk(node)
                    if isinstance(found, ast.List)
                    for item in found.elts
                    if isinstance(item, ast.Name)
                }
    if not names:
        raise Invalid("could not read dashAI's own components")
    return names


def removed_ids():
    return {item["id"] for item in json.loads((ROOT / "removed.json").read_text("utf-8"))}


def entries():
    return sorted((ROOT / "plugins").glob("*.json"))


def entry_repos():
    """Every entry's repo, for ``triage`` (a broken entry is ``load_entry``'s)."""
    repos = []
    for path in entries():
        try:
            repo = json.loads(path.read_text("utf-8"))["repo"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(repo, str) and REPO.match(repo):
            repos.append(repo)
    return repos


def check(paths):
    """A PR: only entries change, each passes, owned by the PR's author."""
    author = os.environ["PR_AUTHOR"].lower()
    report, failed = [], False
    others = json.loads((ROOT / "index.json").read_text("utf-8"))["plugins"]
    dashai = dashai_components()
    for path in paths:
        lines = []
        try:
            entry = load_entry(path, removed_ids())
            owner = entry["repo"].split("/")[0].lower()
            if owner != author:
                try:  # an organization's public member may publish its repos
                    api(f"orgs/{owner}/public_members/{author}")
                except Invalid as error:
                    raise Invalid(f"only {owner} (or a public member of it) can add this repo") from error
            repos = [json.loads(p.read_text("utf-8"))["repo"].lower() for p in entries() if p.name != Path(path).name]
            if entry["repo"].lower() in repos:
                raise Invalid(f"{entry['repo']} is already in the store")
            taken = taken_names(others, entry["id"], dashai)
            ids = {p.stem for p in entries()} - removed_ids()
            listed = describe(entry, lines.append, taken=taken, ids=ids)
            if listed is None:
                raise Invalid("no release passes")
            latest = listed["releases"][0]
            report.append(f"✅ **{entry['id']}** {latest['tag']} ({latest['trust_level']}): {', '.join(listed['components'])}")
        except Invalid as error:
            failed = True
            report.append(f"❌ **{Path(path).name}**: {error}")
        report += lines
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write("\n\n".join(report) + "\n")
    return 1 if failed else 0


def build(full=False):
    """index.json from every entry. A plugin that can't be read now (GitHub
    down, rate limit) keeps what the index had. ``built_at`` is when what it
    lists last changed: a build that changes nothing leaves the file as it was
    (no commit, and dashAI's ETag holds)."""
    path = ROOT / "index.json"
    old = json.loads(path.read_text("utf-8")) if path.is_file() else {"plugins": []}
    kept = {p["id"]: p for p in old["plugins"]}
    removed = json.loads((ROOT / "removed.json").read_text("utf-8"))
    dashai = dashai_components()
    try:
        state = triage(entry_repos())
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"triage failed, each plugin is read with REST: {error}")
        state = {}
    ids = {p.stem for p in entries()} - {item["id"] for item in removed}
    plugins = []
    problems, fine = {}, {}  # id -> (repo, why, tag still offered); id -> tag
    for entry_path in entries():
        print(entry_path.name)
        entry, left_out = None, []
        try:
            entry = load_entry(entry_path, {item["id"] for item in removed})
            before = kept.get(entry["id"], {})
            listed = describe(
                entry,
                print,
                None if full else before or None,
                taken_names(old["plugins"], entry["id"], dashai),
                {release["tag"] for release in before.get("releases", [])},
                left_out,
                state.get(entry["repo"].lower()),
                ids,
            )
        except Invalid as error:
            print(f"  left out: {error}")
            if entry:
                problems[entry["id"]] = (entry["repo"], [str(error)], None)
            continue
        except (urllib.error.URLError, OSError) as error:
            print(f"  kept as it was: {error}")
            if entry_path.stem in kept:
                plugins.append(kept[entry_path.stem])
            continue
        if listed is None:
            print("  left out: no release passes")
            problems[entry["id"]] = (entry["repo"], left_out, None)
            continue
        plugins.append(listed)
        offered = listed["releases"][0]["tag"]
        if left_out:
            problems[entry["id"]] = (entry["repo"], left_out, offered)
        else:
            fine[entry["id"]] = offered
    index = {"schema": 1, "built_at": old.get("built_at"), "plugins": plugins, "removed": removed}
    if index != old:
        index["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Compact: a quarter smaller than indented, and dashAI reads it whole.
    path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8", newline="\n")
    print(f"index.json: {len(plugins)} plugins")
    try:  # index.json is written: a failed notice must not keep it from the commit
        notify(problems, fine)
    except (subprocess.CalledProcessError, urllib.error.URLError, Invalid) as error:
        print(f"could not notify: {error}")
    return 0


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def notify(problems, fine):
    """One open issue per plugin left out (``problems``), mentioning its author:
    opened once, commented only when what fails changes, closed once it passes
    (``fine``). A plugin kept as it was (GitHub down) is in neither."""
    act = bool(os.environ.get("GITHUB_ACTIONS"))
    index = os.environ.get("GITHUB_REPOSITORY") or gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()
    listed = gh("issue", "list", "--author", "github-actions[bot]", "--state", "open", "--limit", "200", "--json", "number,title,body")
    issues = {re.match(r"\[([a-z0-9-]+)\]", i["title"])[1]: i for i in json.loads(listed) if i["title"].startswith("[")}

    def run(*args):
        print(("" if act else "would run: ") + " ".join(args[:3]))
        if act:
            gh(*args)

    for plugin_id, (repo, why, offered) in problems.items():
        owner = api(f"repos/{repo}")["owner"]
        if owner["type"] != "User":  # an organization gets no mention: whoever added the entry
            commits = api(f"repos/{index}/commits?path=plugins/{plugin_id}.json")
            owner = commits[-1]["author"] or owner
        still = f"La tienda sigue ofreciendo **{offered}**." if offered else "El plugin no está en la tienda hasta que una release pase."
        body = "\n".join([
            f"@{owner['login']}, la revisión de la tienda dejó fuera [{plugin_id}](https://github.com/{repo}):",
            "",
            *(f"- {line}" for line in why),
            "",
            f"{still} Qué hacer: [Si una release nueva no aparece](https://github.com/{index}/blob/main/CONTRIBUTING.md#si-una-release-nueva-no-aparece).",
            "Este issue se cierra solo cuando una release nueva pase la revisión.",
        ])
        issue = issues.get(plugin_id)
        if issue is None:
            run("issue", "create", "--title", f"[{plugin_id}] No pasó la revisión de la tienda", "--body", body)
        elif issue["body"].replace("\r\n", "\n").strip() != body.strip():
            run("issue", "comment", str(issue["number"]), "--body", body)
            run("issue", "edit", str(issue["number"]), "--body", body)
        if not act:
            print(body)
    for plugin_id, offered in fine.items():
        if plugin_id in issues:
            run("issue", "close", str(issues[plugin_id]["number"]), "--comment", f"Ya pasa: la tienda ofrece {offered}.")


if __name__ == "__main__":
    command, *args = sys.argv[1:] or ["build"]
    sys.exit(check(args) if command == "check" else build(full="--full" in args))
