"""python -m unittest discover scripts: the build's reuse, without GitHub."""

import unittest
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
            return {"tag": rel["tag_name"], "trust_level": "github_asset"}, FIELDS

        with mock.patch.object(validate, "review", review), mock.patch.object(validate, "api") as api:
            listed = validate.describe({"id": "kit", "repo": "me/kit"}, lambda _: None, known, state=(repo, found))
        api.assert_not_called()  # the repo and its releases came from triage
        self.assertEqual(reviewed, ["v3"])  # v1 is the same file; v0 was left out before and is older
        self.assertEqual([r["tag"] for r in listed["releases"]], ["v3", "v1"])


if __name__ == "__main__":
    unittest.main()
