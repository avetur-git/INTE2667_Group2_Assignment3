# infra/ - security configuration and rollback drill

This folder holds the reviewed infrastructure config for firewall rules,
security groups, and other security settings. Every change is committed to Git
so the rollback pipeline can restore the previous good state.

## Rollback script

Run the prototype rollback from the repository root:

```bash
python EVotingPlatform/infra/rollback.py \
  --repo . \
  --redeploy-command "docker compose up -d --build" \
  --health-url http://localhost:5000/health \
  --max-duration-seconds 900
```

The script does the following:

- checks that the repository has a previous commit to return to
- runs `git revert HEAD` so the bad change is preserved in history
- redeploys the service
- runs the health check
- fails if the service is not healthy or if the rollback exceeds 900 seconds
- records the duration and the audit event

## Drill procedure

1. Create or identify a bad infra change in a branch or temporary commit.
2. Trigger the `Rollback drill` workflow manually or wait for the scheduled cron job.
3. Review the workflow output and the Git log to verify a revert commit was created.
4. Confirm `/health` is healthy after deployment.
5. If the rollback exceeds 15 minutes, stop the drill, treat it as a failed recovery, and escalate to the operations owner.

The rollback drill is expected to be run on a schedule (for example weekly or monthly) and after any major infra change.

## Cloud deployment notes

The production version of this pipeline is the same sequence executed against the cloud environment:

- apply the last known-good infrastructure state with Terraform/OpenTofu
- require manual approval before the destructive apply step in the deployment environment
- run health checks and target-group / service validation after the change
- alert the on-call operations user if the rollback or health check fails
- keep the Git history as the audit trail for the recovered state
