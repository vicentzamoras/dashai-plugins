"""python -m unittest discover scripts: the build's reuse, without GitHub."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import validate

SHA = "a" * 64
FIELDS = {key: [] for key in validate.PLUGIN_FIELDS} | {"name": "Kit", "description": ""}


def release(tag, digest):
    """A release as ``triage`` reads it, with a single .zip."""
    asset = {"name": "kit.zip", "size": 1, "digest": f"sha256:{digest}", "browser_download_url": ""}
    return {"tag_name": tag, "draft": False, "prerelease": False, "published_at": "", "tag_commit": "c" * 40, "assets": [asset]}


class Describe(unittest.TestCase):
    def test_reviews_only_what_changed(self):
        known = {
            "location": "https://github.com/me/kit",
            **FIELDS,
            "releases": [{"tag": "v1", "sha256": SHA, "trust_level": "github_asset", "commit": "c" * 40}],
        }
        repo = {"full_name": "me/kit", "private": False, "archived": False, "license": {"spdx_id": "MIT"}}
        found = [release("v3", "b" * 64), release("v1", SHA), release("v0", "d" * 64)]
        reviewed = []

        def review(_repo, rel, _id):
            reviewed.append(rel["tag_name"])
            return {"tag": rel["tag_name"], "trust_level": "github_asset", "plugin_dependencies": []}, FIELDS

        with mock.patch.object(validate, "review", review), mock.patch.object(validate, "api") as api:
            listed = validate.describe({"id": "kit", "repo": "me/kit"}, lambda _: None, known, state=(repo, found))
        api.assert_not_called()  # the repo and its releases came from triage
        self.assertEqual(reviewed, ["v3"])  # v1 is the same file; v0 was left out before and is older
        self.assertEqual([r["tag"] for r in listed["releases"]], ["v3", "v1"])

    def test_leaves_out_a_release_that_needs_a_plugin_not_in_the_store(self):
        repo = {"full_name": "me/kit", "private": False, "archived": False, "license": {"spdx_id": "MIT"}}
        found = [release("v2", "b" * 64), release("v1", SHA)]
        needs = {"v2": ["text-toolkit"], "v1": ["nlp-core"]}

        def review(_repo, rel, _id):
            tag = rel["tag_name"]
            return {"tag": tag, "trust_level": "github_asset", "plugin_dependencies": needs[tag]}, FIELDS

        left_out = []
        with mock.patch.object(validate, "review", review):
            listed = validate.describe(
                {"id": "kit", "repo": "me/kit"}, lambda _: None, left_out=left_out,
                state=(repo, found), ids={"kit", "nlp-core"},
            )
        self.assertEqual([r["tag"] for r in listed["releases"]], ["v1"])
        self.assertEqual(left_out, ["v2: plugin_dependencies not in the store: text-toolkit; add them first"])


class Manifest(unittest.TestCase):
    def test_plugin_dependencies_follow_dashai_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "kit.py").write_text("class Kit: pass\n")
            manifest = {
                "manifest_version": "1", "id": "kit", "name": "Kit", "version": "1.0.0",
                "min_app_version": "0.1.0", "authors": [{"name": "me"}],
                "components": [{"class": "kit:Kit", "type": "Metric"}],
            }
            for needs, ok in ((["nlp-core"], True), (["NLP"], False), (["a", "a"], False), (["kit"], False)):
                (folder / "manifest.json").write_text(json.dumps({**manifest, "plugin_dependencies": needs}))
                if ok:
                    validate.check_manifest(folder, "kit", "v1.0.0")
                else:
                    with self.assertRaises(validate.Invalid):
                        validate.check_manifest(folder, "kit", "v1.0.0")


if __name__ == "__main__":
    unittest.main()
