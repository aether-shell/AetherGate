import copy
import unittest
from unittest.mock import patch

import ag


class ReleaseContractTest(unittest.TestCase):
    def setUp(self):
        self.manifest = {"schema_version": 1, "repository": ag.REPO, "product": "aethergate",
                         "revision": "a" * 40, "image": ag.IMAGE + "@sha256:" + "b" * 64,
                         "database_schema": "c" * 64,
                         "features": ["api-account-client-restriction", "managed-release"]}

    def test_manifest_rejects_mutable_or_foreign_images(self):
        ag.validate_manifest(self.manifest)
        for image in [ag.IMAGE + ":latest", "weishaw/sub2api@sha256:" + "b" * 64, ag.IMAGE + "@sha256:no"]:
            with self.subTest(image=image), self.assertRaises(ValueError):
                ag.validate_manifest({**self.manifest, "image": image})

    def test_source_and_product_are_required(self):
        for key, value in [("repository", "Wei-Shaw/sub2api"), ("product", "sub2api"), ("revision", "main"), ("features", []), ("database_schema", "")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                ag.validate_manifest({**self.manifest, key: value})

    def test_run_cannot_borrow_another_sha_or_pull_request(self):
        run = {"head_sha": "a" * 40, "head_branch": "main", "event": "push",
               "head_repository": {"full_name": ag.REPO}, "path": ".github/workflows/backend-ci.yml"}
        self.assertTrue(ag.eligible_run(run, "a" * 40, "backend-ci.yml"))
        for key, value in [("head_sha", "b" * 40), ("event", "pull_request"), ("head_branch", "feature"), ("head_repository", {"full_name": "Wei-Shaw/sub2api"})]:
            self.assertFalse(ag.eligible_run({**run, key: value}, "a" * 40, "backend-ci.yml"))

    @patch("ag.api")
    def test_latest_failed_run_blocks_even_if_earlier_green(self, api):
        run = {"id": 1, "head_sha": "a" * 40, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "success",
               "head_repository": {"full_name": ag.REPO}, "path": ".github/workflows/backend-ci.yml", "html_url": "https://example.test"}
        api.return_value = {"workflow_runs": [run, {**run, "id": 2, "conclusion": "failure"}]}
        with self.assertRaises(ValueError):
            ag.gates("a" * 40)

    def test_prepared_hash_changes_on_target_or_baseline_drift(self):
        value = {"target": {"project": "aethergate"}, "baseline": {"container": "old"}, "manifest": self.manifest}
        for key in ["target", "baseline", "manifest"]:
            changed = copy.deepcopy(value)
            changed[key]["changed"] = True
            self.assertNotEqual(ag.canonical_hash(value), ag.canonical_hash(changed))


if __name__ == "__main__":
    unittest.main()
