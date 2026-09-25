#!/usr/bin/env python3
"""GitHub runner only: isolated DB/Redis/app; no real upstream API calls."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def request(port, path, token=None, data=None, extra_headers=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    headers.update(extra_headers or {})
    req = urllib.request.Request("http://" + port + path, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def container_endpoint(name, network):
    # Linux runners can reach the internal bridge directly; no published ports
    # or externally connected container network is needed.
    info = json.loads(command("docker", "inspect", name))[0]
    address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
    if not address:
        raise RuntimeError("No internal address for " + name)
    return address + ":8080"


def main():
    image, revision = sys.argv[1:]
    labels = json.loads(command("docker", "image", "inspect", image))[0]["Config"]["Labels"]
    assert labels["cc.aethergate.product"] == "aethergate"
    assert labels["org.opencontainers.image.source"] == "https://github.com/aether-shell/AetherGate"
    assert labels["org.opencontainers.image.revision"] == revision
    version = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "/app/sub2api", image, "--version")
    assert "AetherGate " in version and revision in version and "product: aethergate" in version
    prefix = "ag-smoke-" + secrets.token_hex(4)
    names = [prefix + "-db", prefix + "-redis", prefix + "-app", prefix + "-upstream"]
    for key in ["POSTGRES_PASSWORD", "DATABASE_PASSWORD", "ADMIN_PASSWORD", "JWT_SECRET", "TOTP_ENCRYPTION_KEY"]:
        os.environ[key] = secrets.token_hex(32)
        print("::add-mask::" + os.environ[key], flush=True)
    os.environ["DATABASE_PASSWORD"] = os.environ["POSTGRES_PASSWORD"]
    command("docker", "pull", "postgres:18-alpine")
    command("docker", "pull", "redis:7-alpine")
    command("docker", "pull", "python:3.12-alpine")
    command("docker", "network", "create", "--internal", prefix)
    try:
        command("docker", "run", "-d", "--name", names[0], "--network", prefix, "--network-alias", "postgres",
                "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=aethergate", "-e", "POSTGRES_DB=aethergate", "postgres:18-alpine")
        command("docker", "run", "-d", "--name", names[1], "--network", prefix, "--network-alias", "redis", "redis:7-alpine")
        command("docker", "run", "-d", "--name", names[3], "--network", prefix, "--network-alias", "fake-upstream",
                "-v", str(Path(__file__).with_name("fake_upstream.py").resolve()) + ":/fake.py:ro", "python:3.12-alpine", "python", "/fake.py")
        for _ in range(30):
            if subprocess.run(["docker", "exec", names[0], "pg_isready", "-U", "aethergate"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        command("docker", "run", "-d", "--name", names[2], "--network", prefix,
                "-e", "AUTO_SETUP=true", "-e", "DATABASE_HOST=postgres", "-e", "DATABASE_USER=aethergate", "-e", "DATABASE_DBNAME=aethergate",
                "-e", "DATABASE_SSLMODE=disable", "-e", "DATABASE_PASSWORD", "-e", "REDIS_HOST=redis", "-e", "ADMIN_EMAIL=admin@aethergate.test",
                "-e", "ADMIN_PASSWORD", "-e", "JWT_SECRET", "-e", "TOTP_ENCRYPTION_KEY", "-e", "RUN_MODE=simple",
                "-e", "SECURITY_URL_ALLOWLIST_ENABLED=false", image)
        port = container_endpoint(names[2], prefix)
        for _ in range(90):
            try:
                status, _ = request(port, "/health")
                if status == 200:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Image did not become healthy")
        status, login = request(port, "/api/v1/auth/login", data={"email": "admin@aethergate.test", "password": os.environ["ADMIN_PASSWORD"]})
        assert status == 200, "Admin login failed"
        token = login["data"]["access_token"]
        print("::add-mask::" + token, flush=True)
        status, ack = request(port, "/api/v1/admin/compliance/accept", token, {
            "language": "en",
            "phrase": "I have read, understood, and agree to the Sub2API Deployment and Operation Compliance Commitment",
        })
        assert status == 200, "Admin compliance acknowledgement failed"
        status, check = request(port, "/api/v1/admin/system/check-updates?force=true", token)
        assert status == 200 and check["data"]["managed"] is True and check["data"]["build_type"] == "aethergate"
        assert not check["data"]["has_update"]
        for path, data in [("update", {}), ("rollback", {}), ("rollback", {"version": "0.2.7"}), ("rollback-versions", None)]:
            status, error = request(port, "/api/v1/admin/system/" + path, token, data)
            assert status == 403 and "SELF_UPDATE_DISABLED" in json.dumps(error), path
        status, group = request(port, "/api/v1/admin/groups", token, {"name": "smoke-openai", "platform": "openai", "rate_multiplier": 1})
        assert status == 200, "Create smoke group failed"
        group_id = group["data"]["id"]
        status, account = request(port, "/api/v1/admin/accounts", token, {"name": "smoke-api-account", "platform": "openai", "type": "apikey", "concurrency": 1,
                                  "credentials": {"api_key": "fixture-only", "base_url": "http://fake-upstream:8080"}, "group_ids": [group_id],
                                  "upstream_billing_probe_enabled": False, "extra": {"codex_cli_only": True, "openai_passthrough": True}})
        assert status == 200 and account["data"]["extra"]["codex_cli_only"] is True, "Account restriction was not saved"
        account_id = account["data"]["id"]
        status, key = request(port, "/api/v1/keys", token, {"name": "smoke-key", "group_id": group_id})
        assert status == 200, "Create smoke key failed"
        api_key = key["data"]["key"]
        print("::add-mask::" + api_key, flush=True)
        upstream = container_endpoint(names[3], prefix)
        before = request(upstream, "/count")[1]["calls"]
        body = {"model": "gpt-5.2", "input": "hello", "stream": False}
        status, error = request(port, "/v1/responses", api_key, body, {"User-Agent": "curl/8"})
        assert status == 403, "Restricted API account accepted an unknown client"
        assert request(upstream, "/count")[1]["calls"] == before, "Denied request reached upstream"
        status, result = request(port, "/v1/responses", api_key, body, {"User-Agent": "codex_cli_rs/0.141.0 (x)", "x-codex-installation-id": "smoke"})
        assert status == 200, "Allowed client was denied"
        assert request(upstream, "/count")[1]["calls"] == before + 1, "Allowed request did not reach fixture"
        status, account = request(port, "/api/v1/admin/accounts/" + str(account_id), token,
                                  {"extra": {"codex_cli_only": False, "openai_passthrough": True}}, method="PUT")
        assert status == 200 and account["data"]["extra"]["codex_cli_only"] is False, "Disable was not persisted"
        status, _ = request(port, "/v1/responses", api_key, body, {"User-Agent": "curl/8"})
        assert status == 200, "Disabled restriction still blocks after cache refresh"
        print("PASS: image identity, setup, login, managed update APIs; API account save/deny with zero calls/allow/disable through real gateway")
    except Exception:
        subprocess.run(["docker", "logs", "--tail", "80", names[2]], check=False)
        raise
    finally:
        for name in reversed(names):
            subprocess.run(["docker", "rm", "-f", "-v", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "rm", prefix], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
