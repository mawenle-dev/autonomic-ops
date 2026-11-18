"""GitHub publisher tests with a fake transport (no network).

Verifies the append-only contract: a new tag creates a new release; an existing
tag is refused; an existing asset name is refused; no DELETE is ever issued;
dry-run performs no mutating request.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autonomic import github_publish as gp  # noqa: E402


class FakeTransport:
    def __init__(self, existing_tags=(), asset_conflict=False):
        self.calls = []
        self.existing_tags = set(existing_tags)
        self.asset_conflict = asset_conflict

    def request(self, method, url, headers, data=None):
        self.calls.append((method, url, data))
        if method == "DELETE":
            raise AssertionError("publisher must never issue DELETE")
        if "/releases/tags/" in url:
            tag = url.rsplit("/", 1)[-1]
            if tag in self.existing_tags:
                return 200, json.dumps({"id": 1, "tag_name": tag,
                                        "html_url": f"https://github.com/o/r/releases/tag/{tag}"}).encode()
            return 404, b'{"message":"Not Found"}'
        if url.endswith("/releases") and method == "POST":
            return 201, json.dumps({"id": 42, "html_url": "https://github.com/o/r/releases/v1.0.1",
                                    "upload_url": "https://uploads.github.com/repos/o/r/releases/42/assets{?name,label}"}).encode()
        if "/assets" in url and method == "POST":
            if self.asset_conflict:
                return 422, b'{"message":"Validation Failed"}'
            name = url.split("name=", 1)[-1]
            return 201, json.dumps({"id": 7, "name": name}).encode()
        return 200, b"{}"


class TestGitHubPublisher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ao_gh_")
        self.asset = os.path.join(self.tmp, "AO-Studio.zip")
        with open(self.asset, "wb") as fh:
            fh.write(b"zipdata")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_makes_no_mutation(self):
        t = FakeTransport()
        p = gp.GitHubPublisher("o", "r", token="x", transport=t)
        r = p.publish("1.0.1", "notes", [self.asset], dry_run=True)
        self.assertTrue(r["ok"] and r["dry_run"])
        self.assertTrue(all(m == "GET" for m, _u, _d in t.calls), t.calls)

    def test_existing_release_refused_no_overwrite(self):
        t = FakeTransport(existing_tags={"v1.0.1"})
        p = gp.GitHubPublisher("o", "r", token="x", transport=t)
        r = p.publish("1.0.1", "notes", [self.asset], dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "release_exists_no_overwrite")
        self.assertFalse(any(m == "POST" for m, _u, _d in t.calls))

    def test_new_release_creates_and_uploads(self):
        t = FakeTransport()
        p = gp.GitHubPublisher("o", "r", token="x", transport=t)
        r = p.publish("1.0.1", "notes", [self.asset], dry_run=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["uploaded"], ["AO-Studio.zip"])
        methods = [m for m, _u, _d in t.calls]
        self.assertIn("POST", methods)
        self.assertNotIn("DELETE", methods)

    def test_asset_conflict_refused_not_deleted(self):
        t = FakeTransport(asset_conflict=True)
        p = gp.GitHubPublisher("o", "r", token="x", transport=t)
        r = p.publish("1.0.1", "notes", [self.asset], dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["failed"][0]["reason"], "asset_exists_no_overwrite")
        self.assertNotIn("DELETE", [m for m, _u, _d in t.calls])

    def test_missing_token_refused_on_real_publish(self):
        p = gp.GitHubPublisher("o", "r", token="", transport=FakeTransport())
        r = p.publish("1.0.1", "notes", [self.asset], dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "missing_token")

    def test_commit_docs_dry_run_no_push(self):
        p = gp.GitHubPublisher("o", "r", token="x", transport=FakeTransport())
        r = p.commit_and_push_docs(self.tmp, ["README.md"], "docs", allow_push=False)
        self.assertTrue(r["ok"] and r["pushed"] is False)

    def test_load_token_from_file(self):
        os.makedirs(os.path.join(self.tmp, ".secrets"))
        with open(os.path.join(self.tmp, ".secrets", "github_token"), "w") as fh:
            fh.write("tok123\n")
        old = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GH_TOKEN")}
        try:
            self.assertEqual(gp.load_token(self.tmp), "tok123")
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
