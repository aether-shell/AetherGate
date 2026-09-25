#!/usr/bin/env python3
"""AetherGate hosted verification, build evidence, and explicit SSH releases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REPO = "aether-shell/AetherGate"
SOURCE = "https://github.com/" + REPO
IMAGE = "ghcr.io/aether-shell/aethergate"
REQUIRED = {
    "backend-ci.yml": {"shell", "test", "frontend", "golangci-lint", "release-helpers"},
    "security-scan.yml": {"backend-security", "frontend-security"},
}


def run(*args, cwd=ROOT, input=None):
    return subprocess.check_output(args, cwd=cwd, input=input, text=True).strip()


def api(path):
    return json.loads(run("gh", "api", "repos/" + REPO + "/" + path))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    require(re.fullmatch(r"[0-9a-f]{40}", value or ""), "A full commit SHA is required")
    return value


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schema_hash(revision):
    entries = run("git", "ls-tree", "-r", sha(revision), "--", "backend/migrations", "backend/ent/schema")
    require(entries, "Missing database schema inventory")
    return hashlib.sha256(entries.encode()).hexdigest()


def source_check(revision, clean=True):
    sha(revision)
    if clean:
        require(not run("git", "status", "--porcelain"), "Commit all changes before verification/build/release")
        require(run("git", "rev-parse", "HEAD") == revision, "Local HEAD changed")
    require(run("git", "remote", "get-url", "origin").removesuffix(".git") in
            {SOURCE, "git@github.com:" + REPO}, "Wrong source repository")
    require(api("commits/" + revision)["sha"] == revision, "Commit is not pushed")
    comparison = api("compare/" + revision + "...main")
    require(comparison["status"] in {"ahead", "identical"}, "Commit must be on canonical main")


def eligible_run(item, revision, workflow):
    return (item.get("head_sha") == revision and item.get("head_branch") == "main"
            and item.get("event") in {"push", "workflow_dispatch"}
            and item.get("head_repository", {}).get("full_name") == REPO
            and item.get("path", "").split("@")[0] == ".github/workflows/" + workflow)


def gates(revision):
    evidence = []
    for workflow, required_jobs in REQUIRED.items():
        runs = api("actions/workflows/" + workflow + "/runs?head_sha=" + sha(revision) + "&per_page=100")["workflow_runs"]
        candidates = [r for r in runs if eligible_run(r, revision, workflow)]
        require(candidates, "No trusted run for " + workflow + " at " + revision)
        latest = max(candidates, key=lambda r: r["id"])
        require(latest["status"] == "completed" and latest["conclusion"] == "success",
                workflow + " is not green: " + latest["html_url"])
        jobs = api("actions/runs/" + str(latest["id"]) + "/jobs?per_page=100")["jobs"]
        passed = {j["name"] for j in jobs if j["status"] == "completed" and j["conclusion"] == "success"}
        require(required_jobs <= passed, "Missing or skipped mandatory jobs: " + workflow)
        evidence.append({"workflow": workflow, "id": latest["id"], "url": latest["html_url"]})
    return evidence


def validate_manifest(manifest):
    require(manifest.get("schema_version") == 1, "Unknown manifest schema")
    require(manifest.get("repository") == REPO and manifest.get("product") == "aethergate", "Wrong product/source")
    sha(manifest.get("revision"))
    require(re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", manifest.get("image", "")), "Immutable AetherGate digest required")
    require(re.fullmatch(r"[0-9a-f]{64}", manifest.get("database_schema", "")), "Missing database schema fingerprint")
    require(set(manifest.get("features", [])) == {"api-account-client-restriction", "managed-release"}, "Feature contract mismatch")


def verified_manifest(run_id):
    build = api("actions/runs/" + str(int(run_id)))
    revision = sha(build["head_sha"])
    require(eligible_run(build, revision, "aethergate-image.yml") and build["conclusion"] == "success", "Build must be a successful canonical main run")
    with tempfile.TemporaryDirectory() as directory:
        run("gh", "run", "download", str(int(run_id)), "--repo", REPO, "--name", "aethergate-release", "--dir", directory)
        manifest = json.loads((Path(directory) / "release.json").read_text())
    validate_manifest(manifest)
    require(manifest["revision"] == revision and manifest["run_id"] == int(run_id), "Artifact/run mismatch")
    gates(revision)
    return manifest


def remote(target, action, manifest, expected=None):
    require(re.fullmatch(r"[A-Za-z0-9_.@-]+", target.get("ssh", "")) and not target["ssh"].startswith("-"), "Explicit SSH target required")
    require(target.get("project", "").startswith("aethergate"), "Target must be an AetherGate project")
    payload = {"target": {k: v for k, v in target.items() if k not in {"ssh", "identity_file"}},
               "action": action, "manifest": manifest, "expected": expected}
    command = "python3 -c " + shlex.quote((ROOT / "tools/aethergate/remote.py").read_text())
    args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if target.get("identity_file"):
        args += ["-i", str(Path(target["identity_file"]).expanduser()), "-o", "IdentitiesOnly=yes"]
    return json.loads(run(*args, target["ssh"], command, input=json.dumps(payload)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "gate", "build", "manifest", "prepare", "release"])
    parser.add_argument("--sha")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--prepared", type=Path, default=Path("aethergate-prepared.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    revision = sha(args.sha or run("git", "rev-parse", "HEAD"))
    if args.command in {"verify", "build", "gate"}:
        source_check(revision, clean=args.command != "gate")
        if args.command == "build":
            gates(revision)
            # The workflow accepts only its own main SHA, closing a workflow/source split.
            require(api("commits/main")["sha"] == revision, "Build the current main commit")
            run("gh", "workflow", "run", "aethergate-image.yml", "--repo", REPO, "--ref", "main", "-f", "commit=" + revision)
            print(SOURCE + "/actions/workflows/aethergate-image.yml")
            return
        result = {"revision": revision, "checks": gates(revision)}
    elif args.command == "manifest":
        contract = json.loads((ROOT / "deploy/aethergate/customizations.json").read_text())
        result = {**contract, "revision": revision, "image": args.image, "database_schema": schema_hash(revision),
                  "run_id": int(os.environ["GITHUB_RUN_ID"]), "checks": gates(revision)}
        validate_manifest(result)
    elif args.command == "prepare":
        require(args.run_id and args.target, "--run-id and --target are required")
        manifest = verified_manifest(args.run_id)
        source_check(manifest["revision"])
        require(schema_hash(manifest["revision"]) == manifest["database_schema"], "Schema/source mismatch")
        target = json.loads(args.target.read_text())
        baseline = remote(target, "prepare", manifest)
        result = {"manifest": manifest, "target_hash": canonical_hash(target), "baseline": baseline}
        result["seal"] = canonical_hash(result)
        args.output = args.prepared
    else:
        require(args.execute, "Use --execute (make ag-release AG_EXECUTE=1) to switch the app")
        require(args.target, "--target is required")
        prepared = json.loads(args.prepared.read_text())
        seal = prepared.pop("seal")
        require(seal == canonical_hash(prepared), "Prepared manifest changed")
        target = json.loads(args.target.read_text())
        require(canonical_hash(target) == prepared["target_hash"], "Target configuration changed; prepare again")
        manifest = verified_manifest(prepared["manifest"]["run_id"])
        require(manifest == prepared["manifest"], "Build evidence changed; prepare again")
        source_check(manifest["revision"])
        result = remote(target, "release", manifest, prepared["baseline"])
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("AetherGate stopped: " + str(error), file=sys.stderr)
        sys.exit(1)
