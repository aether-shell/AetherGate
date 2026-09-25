# AetherGate

AetherGate directly follows Wei-Shaw/sub2api. Its only business customization is
client restriction for **OpenAI upstream API Key accounts**. No RootFlow database
or other TokenRouter features are imported.

## Client restriction

Create/edit an OpenAI API Key account and enable the Codex client restriction.
The account uses sub2api's global official-client, allow/deny-list, version and
engine-fingerprint settings, including its app-server option. Each account has
its own switch; client lists are global. The switch is off by default.

Responses, Chat Completions, Messages, images (including multipart edits),
embeddings, token-counting, search, Seedance and Responses WebSocket ingress
check the account before forwarding. Live currently only supports upstream
OAuth accounts and is outside the API Key extension. Other providers and
setup-token behavior are unchanged. Header-based client recognition retains
upstream's limitations; it is not cryptographic client attestation. The existing
administrator setting gateway.force_codex_cli intentionally bypasses this
restriction and should remain false when enforcement is required.

No SQL migration is added: codex_cli_only and codex_cli_only_allow_app_server
remain in accounts.extra, including scheduler-cache projections.

## Cloud builds

The supported artifact is a linux/amd64 image from
ghcr.io/aether-shell/aethergate. All Go/Vue compilation, application tests and
Docker smoke checks run on standard GitHub-hosted runners. No local Docker,
production build machine, paid API, or production SSH secret is required by CI.

1. Commit and push to main. CI and Security Scan run on that commit.
2. Run make ag-verify locally to query the exact commit's required workflows.
   Pending/failed/skipped jobs block the build. To rerun a failed job use GitHub
   Actions' rerun-failed-jobs control; verification does not compile locally.
3. Run make ag-build to dispatch AetherGate Image for the current main SHA.
4. The workflow validates the same-SHA checks, builds and tests the image in an
   isolated network with fresh PostgreSQL/Redis, then pushes it with a temporary
   GITHUB_TOKEN. It uploads release.json as the aethergate-release artifact.
5. Use the manifest's full digest, not a mutable tag, for deployment. Keep the
   running and previous image digests; Actions evidence currently lasts 90 days.

The manifest binds product, source SHA, features, schema fingerprint, successful
workflow runs, build run ID and image digest. The binary and OCI image labels
both identify AetherGate and the full SHA. No latest tag is published.

## Managed upgrades

The application rejects official binary update, rollback and version-download
APIs with SELF_UPDATE_DISABLED before acquiring the update lock or downloading.
The version UI links to AetherGate Actions and never offers the inherited
official installer. This holds even with stale official update cache data.
The inherited Release workflow and binary installer are disabled for AetherGate.
Legacy upstream Compose files and other upstream installation documentation are
reference material; use deploy/aethergate/compose.yaml for AetherGate.

## Prepare and release to an explicitly configured host

No production target is predefined. Install Docker Compose v2, Python 3 and a
trusted SSH host key on the chosen linux/amd64 host. Create a dedicated directory,
copy compose.yaml there, and configure its required secrets in a mode-0600 .env
outside Git. Set AETHERGATE_IMAGE to the selected manifest digest. Use unique
project/storage identities, an HTTPS reverse proxy, and fresh database volumes.
This procedure does not migrate an existing TokenRouter database.

Create aethergate-target.json next to compose.yaml with exactly these ownership
fields: product=aethergate, project=aethergate, service=aethergate,
database_service=postgres. Copy target.example.json outside the repository and
fill in the actual SSH alias, key path, directory and loopback health port.
The hostname is never inferred from Pro/TR configuration.

For a first install, start only the infrastructure explicitly on the host:
docker compose --project-name aethergate -f compose.yaml up -d postgres redis.
The release tool requires an empty public database schema and an absent app.

Local commands (only SSH orchestration and GitHub queries):

    make ag-prepare AG_RUN_ID=<successful-image-run> AG_TARGET=/secure/target.json
    make ag-release AG_TARGET=/secure/target.json AG_EXECUTE=1

prepare pulls and inspects the candidate and runs its version command without
network. It does not switch the application. It records the current container,
effective Compose configuration hash, source and build evidence. release repeats
the checks and rejects configuration/target/baseline drift. An exclusive host
lock prevents two managed releases from running together. Only the app service
is replaced; database and Redis are not rebuilt or restarted.

The host keeps .aethergate-image.json and .aethergate-release.json next to
compose.yaml. Always include the image override when manually inspecting this
deployment. These receipts contain no credentials. Do not edit them by hand.

For updates set mode=update, fresh_database=false and rollback_compatible=true
only after reviewing application/data compatibility. The tool also requires
identical SQL/Ent schema fingerprints before allowing automatic image rollback.
On failure it restores the prior digest and checks health. Schema-changing
updates stop for a separate reviewed backup/migration procedure; this first
release tool deliberately does not guess a database recovery contract. An
interrupted or unsuccessful first install requires inspection before retrying.

## Following upstream

Merge a fixed upstream commit into AetherGate, review changes to forwarding and
update/release entrypoints, then run the same cloud gates and publish a new
AetherGate image. Keep original module paths, upstream attribution and license.
Never deploy official upstream tags directly over an AetherGate instance.
