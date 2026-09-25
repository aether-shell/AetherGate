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


if __name__ == "__main__":
    unittest.main()
