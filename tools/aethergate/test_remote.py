import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import remote


class TargetContractTest(unittest.TestCase):
    def setUp(self):
        self.target = {"product": "aethergate", "project": "aethergate", "mode": "install", "service": "aethergate",
                       "database_service": "postgres", "compose_file": "/opt/aethergate/compose.yaml", "health_url": "http://127.0.0.1:8080/health"}

    def test_target_identity_and_explicit_modes(self):
        remote.validate_target(self.target)
        remote.validate_target({**self.target, "mode": "update"})
        for key, value in [("product", "pro"), ("project", "tokenrouter-pro"), ("mode", "auto"), ("compose_file", "compose.yaml"), ("health_url", "https://production.example/health")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                remote.validate_target({**self.target, key: value})

    def test_rollback_needs_prior_receipt_same_schema_and_explicit_compatibility(self):
        current = {"database_schema": "a"}
        self.assertTrue(remote.compatible(current, current, True))
        self.assertFalse(remote.compatible(None, current, True))
        self.assertFalse(remote.compatible(current, current, False))
        self.assertFalse(remote.compatible(current, {"database_schema": "b"}, True))

    @patch("remote.command")
    def test_foreign_images_refused_before_docker(self, command):
        with self.assertRaises(ValueError):
            remote.image_identity("weishaw/sub2api:latest", "a" * 40)
        command.assert_not_called()

    @patch("remote.command")
    def test_label_revision_mismatch_refuses_binary_execution(self, command):
        command.side_effect = ["", '[{"Config":{"Labels":{"cc.aethergate.product":"aethergate","org.opencontainers.image.source":"https://github.com/aether-shell/AetherGate","org.opencontainers.image.revision":"wrong"}}}]']
        with self.assertRaises(ValueError):
            remote.image_identity(remote.IMAGE + "@sha256:" + "b" * 64, "a" * 40)
        self.assertEqual(command.call_count, 2)

    def exercise_deployment(self, *, mode="install", tables="0", changed=False, unhealthy=False):
        with tempfile.TemporaryDirectory() as directory:
            target = {**self.target, "compose_file": directory + "/compose.yaml", "mode": mode,
                      "fresh_database": True, "rollback_compatible": True}
            identity = {k: target[k] for k in ("product", "project", "service", "database_service")}
            Path(directory, "aethergate-target.json").write_text(json.dumps(identity))
            Path(directory, "compose.yaml").write_text("services: {}")
            old = {"product": "aethergate", "repository": "aether-shell/AetherGate", "revision": "a" * 40,
                   "image": remote.IMAGE + "@sha256:" + "b" * 64, "database_schema": "schema"}
            manifest = {**old, "revision": "c" * 40, "image": remote.IMAGE + "@sha256:" + "d" * 64}
            if mode == "update":
                Path(directory, ".aethergate-release.json").write_text(json.dumps({"manifest": old}))
                Path(directory, ".aethergate-image.json").write_text(json.dumps({"services": {"aethergate": {"image": old["image"]}}}))
            config = {"services": {"aethergate": {"image": old["image"], "labels": {"cc.aethergate.product": "aethergate"}},
                                   "postgres": {"labels": {"cc.aethergate.product": "aethergate"}}}}
            state = {"image": old["image"] if mode == "update" else None, "ups": []}

            def fake_command(*args):
                if args == ("uname", "-m"):
                    return "x86_64"
                if "config" in args:
                    return json.dumps(config)
                if "ps" in args:
                    return "container" if state["image"] else ""
                if args[:2] == ("docker", "inspect"):
                    new = state["image"] == manifest["image"]
                    return json.dumps([{"Id": "new-container" if new else "old-container", "Image": "new-id" if new else "old-id",
                                        "Config": {"Image": state["image"], "Labels": {"com.docker.compose.project": "aethergate", "cc.aethergate.product": "aethergate"}},
                                        "State": {"Health": {"Status": "unhealthy" if new and unhealthy else "healthy"}}}])
                if "psql" in args:
                    return tables
                if "up" in args:
                    state["image"] = json.loads(Path(directory, ".aethergate-image.json").read_text())["services"]["aethergate"]["image"]
                    state["ups"].append(args)
                    return ""
                raise AssertionError(args)

            with patch("remote.command", side_effect=fake_command), patch("remote.image_identity", return_value="new-id"), patch("remote.time.sleep"), patch("remote.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.status = 200
                baseline = remote.deploy({"target": target, "manifest": manifest, "action": "prepare"})
                self.assertEqual(state["ups"], [], "prepare must not switch services")
                if changed:
                    config["services"]["aethergate"]["environment"] = {"NEW_CONFIG": "changed"}
                try:
                    result = remote.deploy({"target": target, "manifest": manifest, "action": "release", "expected": baseline})
                except ValueError:
                    if changed:
                        self.assertEqual(state["ups"], [], "drift must refuse before switch")
                    if unhealthy and mode == "update":
                        self.assertEqual(state["image"], old["image"])
                        self.assertEqual(json.loads(Path(directory, ".aethergate-release.json").read_text())["manifest"], old)
                    raise
                self.assertTrue(result["healthy"])
                self.assertEqual(result["manifest"], manifest)
                self.assertTrue(all("--no-deps" in call and call[-1] == "aethergate" for call in state["ups"]))

    def test_install_and_update_execute_only_application(self):
        self.exercise_deployment()
        self.exercise_deployment(mode="update")

    def test_first_install_refuses_populated_database(self):
        with self.assertRaisesRegex(ValueError, "empty public"):
            self.exercise_deployment(tables="1")

    def test_prepare_baseline_drift_prevents_release(self):
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            self.exercise_deployment(mode="update", changed=True)

    def test_failed_update_restores_prior_compatible_image(self):
        with self.assertRaisesRegex(ValueError, "restored and healthy"):
            self.exercise_deployment(mode="update", unhealthy=True)


if __name__ == "__main__":
    unittest.main()
