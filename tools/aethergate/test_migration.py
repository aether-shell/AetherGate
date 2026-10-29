import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import remote


class MigrationEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        release = self.root / "releases" / "candidate"
        release.mkdir(parents=True)
        self.path = release / "evidence.json"
        self.target = {"project": "aethergate", "service": "aethergate", "migration_evidence": str(self.path)}
        self.previous = {"revision": "a" * 40, "image": remote.IMAGE + "@sha256:" + "a" * 64, "database_schema": "a" * 64}
        self.manifest = {"revision": "b" * 40, "image": remote.IMAGE + "@sha256:" + "b" * 64, "database_schema": "b" * 64}
        now = datetime.now(timezone.utc)
        self.current = {"Id": "old-container", "State": {"Status": "exited", "Running": False, "FinishedAt": (now - timedelta(seconds=30)).isoformat()},
                        "NetworkSettings": {"Networks": {"aethergate-network": {}}}}
        self.config = {"services": {"aethergate": {"ports": [{"target": 8080}]}}}
        before = [{"filename": "241_custom.sql", "checksum": "a" * 64}]
        self.proof = {"schema_version": 1, "project": "aethergate", "previous": self.previous,
                      "candidate": self.manifest, "config_hash": remote.digest(self.config),
                      "container": self.current["Id"], "created_at": now.isoformat(),
                      "checks": dict.fromkeys(["restore", "upgrade", "downgrade", "configuration", "customizations"], True),
                      "migrations_before": before, "migrations_after": before + [{"filename": "248_next.sql", "checksum": "b" * 64}]}
        for name in ("backup", "rehearsal_backup"):
            path = release / (name + ".dump")
            path.write_bytes(b"fixture backup")
            path.chmod(0o600)
            self.proof[name] = {"path": str(path), "sha256": remote.file_hash(path)}

    def validate(self, proof=None):
        self.path.write_text(json.dumps(self.proof if proof is None else proof))
        self.path.chmod(0o600)
        with patch("remote.database_migrations", return_value=self.proof["migrations_before"]), patch("remote.migration_firewall") as firewall:
            result = remote.migration_evidence(self.target, self.previous, self.manifest, self.current, self.config, [], self.root)
            firewall.assert_called_once_with(self.current)
            return result

    def test_valid_bound_private_checkpoint(self):
        self.assertEqual(self.validate(), self.proof)

    def test_missing_or_false_rehearsal_checks_are_rejected(self):
        for check in self.proof["checks"]:
            for value in (False, None, "true"):
                proof = copy.deepcopy(self.proof)
                proof["checks"][check] = value
                with self.subTest(check=check, value=value), self.assertRaisesRegex(ValueError, "rehearsals"):
                    self.validate(proof)

    def test_changed_source_container_config_or_ledger_are_rejected(self):
        for field, value in [("previous", self.manifest), ("candidate", self.previous), ("container", "another"),
                             ("config_hash", "changed"), ("migrations_before", [])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate({**self.proof, field: value})

    def test_live_app_is_rejected_even_with_a_complete_proof(self):
        self.current["State"].update(Status="running", Running=True)
        with self.assertRaisesRegex(ValueError, "Stop and drain"):
            self.validate()

    def test_old_or_future_checkpoint_is_rejected(self):
        for delta in (-7200, 7200):
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, "checkpoint is stale"):
                self.validate({**self.proof, "created_at": (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()})

    def test_backup_tampering_is_rejected(self):
        Path(self.proof["backup"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.validate()

    def test_no_removed_or_rewritten_migrations(self):
        after = [{"filename": "241_custom.sql", "checksum": "c" * 64}, self.proof["migrations_after"][1]]
        with self.assertRaisesRegex(ValueError, "preserve all"):
            self.validate({**self.proof, "migrations_after": after})

    def test_additional_exposed_port_is_rejected(self):
        self.config["services"]["aethergate"]["ports"].append({"target": 9000})
        with self.assertRaisesRegex(ValueError, "only application port"):
            self.validate()

    @patch("remote.command")
    def test_guard_checks_both_forwarded_and_host_traffic(self, command):
        command.side_effect = [json.dumps([{"EnableIPv6": False, "IPAM": {"Config": [{"Subnet": "172.22.0.0/24"}]}}]), "", ""]
        remote.migration_firewall(self.current)
        calls = [call.args for call in command.call_args_list]
        self.assertEqual(calls[1][0:3], ("iptables", "-C", "DOCKER-USER"))
        self.assertEqual(calls[2][0:3], ("iptables", "-C", "OUTPUT"))
        self.assertIn("172.22.0.0/24", calls[2])

    @patch("remote.command")
    def test_missing_firewall_rule_fails_closed(self, command):
        command.side_effect = [json.dumps([{"IPAM": {"Config": [{"Subnet": "172.22.0.0/24"}]}}]), ValueError("missing firewall")]
        with self.assertRaisesRegex(ValueError, "missing firewall"):
            remote.migration_firewall(self.current)


if __name__ == "__main__":
    unittest.main()
