"""Executed over SSH; stdout contains only a redacted deployment receipt."""
import fcntl
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

IMAGE = "ghcr.io/aether-shell/aethergate"
SOURCE = "https://github.com/aether-shell/AetherGate"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    # Docker/Compose errors can contain interpolated environment values.
    require(result.returncode == 0, "Remote command failed: " + args[0])
    return result.stdout.strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def immutable(image):
    return bool(re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", image or ""))


def image_identity(image, revision):
    require(immutable(image), "Only immutable AetherGate images can run")
    command("docker", "pull", image)
    item = json.loads(command("docker", "image", "inspect", image))[0]
    labels = item["Config"].get("Labels") or {}
    require(labels.get("cc.aethergate.product") == "aethergate" and labels.get("org.opencontainers.image.source") == SOURCE,
            "Wrong image product/source")
    require(labels.get("org.opencontainers.image.revision") == revision, "Image revision mismatch")
    require(item["Architecture"] == "amd64", "Unsupported image architecture")
    version = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "/app/sub2api", image, "--version")
    require("AetherGate " in version and "product: aethergate" in version and revision in version, "Binary identity mismatch")
    return item["Id"]


def compatible(previous, manifest, declared):
    return bool(previous and declared is True and previous.get("database_schema") == manifest.get("database_schema"))


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def database_migrations(base, target, services):
    db = services[target["database_service"]].get("environment", {})
    query = ("SELECT COALESCE(json_agg(m ORDER BY filename),'[]'::json) FROM "
             "(SELECT filename,checksum FROM schema_migrations) m")
    return json.loads(command(*base, "exec", "-T", target["database_service"], "psql", "-X", "-qAt",
                              "-v", "ON_ERROR_STOP=1", "-U", db.get("POSTGRES_USER", "postgres"),
                              "-d", db.get("POSTGRES_DB", "postgres"), "-c", query))


def migration_firewall(current):
    networks = current.get("NetworkSettings", {}).get("Networks", {})
    require(len(networks) == 1, "Migration requires one dedicated application network")
    network = json.loads(command("docker", "network", "inspect", next(iter(networks))))[0]
    require(not network.get("EnableIPv6"), "IPv6 migration isolation is not implemented")
    configs = network.get("IPAM", {}).get("Config", [])
    require(len(configs) == 1, "Ambiguous migration network subnet")
    subnet = str(ipaddress.IPv4Network(configs[0]["Subnet"]))
    require(ipaddress.IPv4Network(subnet).is_private, "Private migration subnet required")
    # These rules are installed by the operator before draining/stopping the app.
    # Both forwarded client traffic and host-originated reverse-proxy traffic fail closed.
    for chain in ("DOCKER-USER", "OUTPUT"):
        command("iptables", "-C", chain, "-d", subnet, "-p", "tcp", "--dport", "8080",
                "-m", "conntrack", "--ctstate", "NEW", "-m", "comment", "--comment",
                "aethergate-schema-maintenance", "-j", "REJECT")


def migration_evidence(target, previous, manifest, current, config, base, directory):
    app = config["services"][target["service"]]
    require(str(app.get("environment", {}).get("SERVER_PORT", "8080")) == "8080" and
            all(str(p.get("target")) == "8080" for p in app.get("ports", [])),
            "Migration isolation supports only application port 8080")
    require(all(n.startswith(target["project"] + "-") for n in current.get("NetworkSettings", {}).get("Networks", {})),
            "Migration requires a dedicated project network")
    path = Path(target.get("migration_evidence", ""))
    require(path.is_absolute() and path.resolve().is_relative_to(directory / "releases"),
            "Migration evidence must be under this deployment's releases directory")
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, "Private migration evidence required")
    proof = json.loads(path.read_text())
    require(proof.get("schema_version") == 1 and proof.get("project") == target["project"], "Wrong migration evidence identity")
    keys = ("revision", "image", "database_schema")
    require(proof.get("previous") == {k: previous[k] for k in keys} and
            proof.get("candidate") == {k: manifest[k] for k in keys}, "Migration evidence/source mismatch")
    require(previous["database_schema"] != manifest["database_schema"], "Use update mode for the same schema")
    require(proof.get("config_hash") == digest(config) and proof.get("container") == current["Id"], "Migration checkpoint baseline changed")
    require(current["State"].get("Status") == "exited" and not current["State"].get("Running"),
            "Stop and drain the old application before creating the final migration checkpoint")
    created = datetime.fromisoformat(proof["created_at"].replace("Z", "+00:00"))
    stopped = datetime.fromisoformat(current["State"]["FinishedAt"].replace("Z", "+00:00"))
    require(created.tzinfo is not None and stopped <= created and
            0 <= (datetime.now(timezone.utc) - created).total_seconds() <= 3600, "Migration checkpoint is stale or predates shutdown")
    for name in ("backup", "rehearsal_backup"):
        item = proof[name]
        backup = Path(item["path"])
        require(backup.is_absolute() and backup.resolve().is_relative_to(directory / "releases") and
                backup.is_file() and backup.stat().st_mode & 0o077 == 0, "Private deployment backup required")
        require(backup.stat().st_size > 0 and file_hash(backup) == item["sha256"], "Migration backup checksum mismatch")
        if name == "backup":
            require(backup.stat().st_mtime >= stopped.timestamp(), "Final backup predates application shutdown")
    checks = proof.get("checks", {})
    require(all(checks.get(k) is True for k in ("restore", "upgrade", "downgrade", "configuration", "customizations")),
            "Migration and restore rehearsals must pass before release")
    before = database_migrations(base, target, config["services"])
    require(proof.get("migrations_before") == before, "Live migration ledger differs from checkpoint")
    after = proof.get("migrations_after", [])
    require(len(after) > len(before) and len({m["filename"] for m in after}) == len(after) and
            all(m in after for m in before), "Migration rehearsal must preserve all existing checksums")
    require(all(re.fullmatch(r"[0-9a-f]{64}", m.get("checksum", "")) for m in after), "Invalid migration checksum")
    migration_firewall(current)
    return proof


def validate_target(target):
    require(target.get("product") == "aethergate", "Explicit product is required")
    require(re.fullmatch(r"aethergate(?:-[a-z0-9-]+)?", target.get("project", "")), "Wrong project")
    require(target.get("mode") in {"install", "update", "migrate"}, "Explicit install/update/migrate mode required")
    require(target.get("service") == "aethergate", "Application service must be aethergate")
    require(re.fullmatch(r"[a-zA-Z0-9_-]+", target.get("database_service", "")), "Database service required")
    require(target["database_service"] != target["service"], "Database/application services must differ")
    require(Path(target.get("compose_file", "")).is_absolute(), "Absolute Compose path required")
    health = urlsplit(target.get("health_url", ""))
    try:
        address = ipaddress.ip_address(health.hostname or "")
        port = health.port
    except ValueError:
        raise ValueError("Private IP health endpoint required") from None
    require(health.scheme == "http" and address.version == 4 and address.is_private and port and
            health.path == "/health" and not (health.username or health.password or health.query or health.fragment),
            "Private IP health endpoint required")


def health_target_matches_container(target, container):
    host = urlsplit(target["health_url"]).hostname
    if host == "127.0.0.1":
        return True
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    return host in {network.get("IPAddress") for network in networks.values()}


def deploy(payload):
    validate_target(payload["target"])
    directory = Path(payload["target"]["compose_file"]).resolve().parent
    with open(directory / ".aethergate-release.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return deploy_locked(payload)


def deploy_locked(payload):
    target, manifest = payload["target"], payload["manifest"]
    validate_target(target)
    require(manifest.get("product") == "aethergate" and manifest.get("repository") == "aether-shell/AetherGate", "Wrong manifest")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", "")), "Full revision required")
    require(immutable(manifest.get("image")), "Immutable image required")
    compose_file = Path(target["compose_file"]).resolve()
    directory = compose_file.parent
    identity = json.loads((directory / "aethergate-target.json").read_text())
    require(identity == {k: target[k] for k in ("product", "project", "service", "database_service")}, "Host identity file does not match target")
    require(command("uname", "-m") == "x86_64", "First release supports linux/amd64")
    base = ["docker", "compose", "--project-name", target["project"], "-f", str(compose_file)]
    override = directory / ".aethergate-image.json"
    receipt_file = directory / ".aethergate-release.json"
    effective = base + (["-f", str(override)] if override.exists() else [])
    config = json.loads(command(*effective, "config", "--format", "json"))
    services = config["services"]
    require(target["service"] in services and target["database_service"] in services, "Compose service mismatch")
    for name in (target["service"], target["database_service"]):
        require(services[name].get("labels", {}).get("cc.aethergate.product") == "aethergate", "Compose ownership labels required")
    require(not services[target["service"]].get("build"), "Server-side builds are prohibited")
    container_ids = command(*effective, "ps", "--all", "--quiet", target["service"]).split()
    require(len(container_ids) <= 1, "Ambiguous app containers")
    current = json.loads(command("docker", "inspect", container_ids[0]))[0] if container_ids else None
    previous = json.loads(receipt_file.read_text())["manifest"] if receipt_file.exists() else None
    migration = None
    migration_state = directory / ".aethergate-migration-state.json"
    if migration_state.exists():
        require(json.loads(migration_state.read_text()).get("status") == "verified",
                "An interrupted migration requires operator recovery before any release")
    if target["mode"] == "install":
        require(current is None and previous is None, "Install cannot overwrite an existing app")
        require(target.get("fresh_database") is True, "First install must declare a new database")
        db = services[target["database_service"]].get("environment", {})
        tables = command(*effective, "exec", "-T", target["database_service"], "psql", "-U", db.get("POSTGRES_USER", "postgres"),
                         "-d", db.get("POSTGRES_DB", "postgres"), "-Atc", "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        require(tables == "0", "First install requires an empty public database schema")
    else:
        require(current is not None and previous is not None, "Updates require a prior managed release receipt")
        labels = current["Config"].get("Labels", {})
        require(labels.get("com.docker.compose.project") == target["project"] and labels.get("cc.aethergate.product") == "aethergate", "Current container identity mismatch")
        require(current["Config"]["Image"] == previous["image"] and immutable(previous["image"]), "Current deployment differs from receipt")
        if target["mode"] == "migrate":
            migration = migration_evidence(target, previous, manifest, current, config, effective, directory)
        else:
            require(current["State"].get("Health", {}).get("Status") == "healthy", "Current app is not healthy")
            require(health_target_matches_container(target, current), "Health endpoint does not address the app container")
            require(compatible(previous, manifest, target.get("rollback_compatible")), "Schema changed or compatibility not declared: a separate backup/migration procedure is required")
    snapshot = {"config_hash": digest(config), "container": current["Id"] if current else None,
                "image_id": current["Image"] if current else None, "previous": previous,
                "identity_hash": digest(identity)}
    if migration:
        snapshot["migration_hash"] = digest(migration)
    if payload["action"] == "release":
        require(snapshot == payload.get("expected"), "Target/configuration/baseline changed; prepare again")
    expected_image_id = image_identity(manifest["image"], manifest["revision"])
    if payload["action"] == "prepare":
        return snapshot
    require(payload["action"] == "release", "Unknown action")
    if migration:
        migration_firewall(current)
        migration_state.write_text(json.dumps({"status": "started", "evidence": migration}))
    old_override = override.read_bytes() if override.exists() else None
    new_override = {"services": {target["service"]: {"image": manifest["image"]}}}
    temporary = override.with_suffix(".tmp")
    temporary.write_text(json.dumps(new_override))
    os.replace(temporary, override)
    effective = base + ["-f", str(override)]
    try:
        command(*effective, "up", "-d", "--no-deps", "--pull", "never", target["service"])
        healthy = False
        for _ in range(60):
            ids = command(*effective, "ps", "--quiet", target["service"]).split()
            if len(ids) == 1:
                running = json.loads(command("docker", "inspect", ids[0]))[0]
                if running["Image"] == expected_image_id and running["State"].get("Health", {}).get("Status") == "healthy":
                    if migration:
                        migration_firewall(running)
                        response = json.loads(command("docker", "exec", ids[0], "wget", "-qO-", "http://127.0.0.1:8080/health"))
                        healthy = response.get("status") == "ok"
                        if healthy:
                            break
                    require(health_target_matches_container(target, running), "Health endpoint does not address the app container")
                    try:
                        with urllib.request.urlopen(target["health_url"], timeout=3) as response:
                            healthy = response.status == 200
                    except OSError:
                        pass
                    if healthy:
                        break
            time.sleep(2)
        require(healthy, "New application did not become healthy")
        if migration:
            require(database_migrations(effective, target, services) == migration["migrations_after"],
                    "Migrated ledger differs from the rehearsal; leave traffic blocked")
    except Exception:
        if migration:
            migration_state.write_text(json.dumps({"status": "failed", "evidence": migration}))
            command(*effective, "stop", "-t", "30", target["service"])
            raise ValueError("Migration failed; candidate stopped and traffic remains blocked. Restore the checkpoint before restarting; no automatic database rollback") from None
        if compatible(previous, manifest, target.get("rollback_compatible")) and old_override:
            image_identity(previous["image"], previous["revision"])
            override.write_bytes(old_override)
            command(*effective, "up", "-d", "--no-deps", "--pull", "never", target["service"])
            # A failed recovery must never be reported as a successful rollback.
            command(*effective, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", target["service"])
            raise ValueError("Release failed; previous compatible AetherGate image restored and healthy") from None
        raise ValueError("Release failed; inspect target before retry. No compatible automatic rollback was available") from None
    receipt = {"manifest": manifest, "previous": previous, "image_id": expected_image_id, "healthy": True}
    if migration:
        receipt["migration"] = {"evidence_hash": digest(migration), "backup": migration["backup"], "traffic_blocked": True}
    temporary = receipt_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2))
    os.replace(temporary, receipt_file)
    if migration:
        migration_state.write_text(json.dumps({"status": "verified", "evidence": migration}))
    return receipt


if __name__ == "__main__":
    try:
        print(json.dumps(deploy(json.load(sys.stdin))))
    except Exception as error:
        print("AetherGate deployment stopped: " + str(error), file=sys.stderr)
        sys.exit(1)
