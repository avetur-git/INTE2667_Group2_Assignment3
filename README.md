# INTE2667_Group2_Assignment3
Secure Software Systems - Assignment 3 - Group 2

## Project

This repository contains the EVotingPlatform Flask application and the supporting infrastructure rollback prototype for Requirement 2.16.

## Local setup

```bash
cd EVotingPlatform
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

To seed demo data:

```bash
flask --app run seed-demo
```

To run the app locally:

```bash
python run.py
```

## Run tests

```bash
cd EVotingPlatform
python -m pytest -q
```

For the focused rollback and RBAC checks:

```bash
python -m pytest tests/test_rollback.py tests/test_rbac.py tests/test_candidate_history.py -q
```

## Rollback drill

The infrastructure rollback workflow is implemented in `EVotingPlatform/infra/rollback.py`.

Example:

```bash
cd EVotingPlatform
python infra/rollback.py \
  --repo . \
  --redeploy-command "docker compose up -d --build" \
  --health-url http://localhost:5000/health \
  --max-duration-seconds 900
```

Behavior:
- refuses to run if the repository has no previous commit to revert
- runs `git revert HEAD` so Git history is preserved
- redeploys the service
- checks `/health`
- fails if the service is not healthy or if the drill exceeds 900 seconds
- records the duration and writes a rollback audit event

The scheduled GitHub Actions drill is defined in `.github/workflows/rollback-drill.yml` and can also be run manually from the Actions tab.

## Manual drill procedure

1. Introduce a bad infra/security change in a temporary branch or a throwaway commit.
2. Run the rollback workflow or invoke the rollback script directly.
3. Verify the repository contains a revert commit and the service is healthy.
4. Review the workflow summary and audit log entries.
5. If the rollback exceeds 15 minutes, escalate to the operations owner and treat the drill as failed.

## Cloud deployment notes

The cloud version of the same recovery process uses the same Git-based rollback pattern, but against the deployed environment. In production, this should include:
- Terraform/OpenTofu apply for the last known-good configuration
- approval gates before destructive infrastructure changes
- health checks and application verification after redeploy
- alerting when rollback or health validation fails
- preserving the Git audit trail for every recovery action
