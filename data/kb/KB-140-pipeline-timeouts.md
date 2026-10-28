---
id: KB-140
title: "Runbook: data pipeline jobs failing or timing out after maintenance"
systems: [Jenkins, Snowflake, Tableau]
symptoms: [job_failure, slow]
audience: employee
status: active
updated: 2026-10-01
---
## Check first
1. Recent change log for Jenkins, Snowflake and network changes.
2. The failing stage and the exact error of the last runs.

## Tell causes apart by the error
- **Timeout connecting to a host** (connect ... timed out): network path blocked. Typical after firewall changes or a new allow-list on the destination. Needs the Network team and/or Data Platform.
- **Authentication failed / invalid credentials**: the job uses an old service account secret after a rotation. Needs Data Platform to update the Jenkins credential.
- **Job runs but downstream report is stale**: refresh failed or source data is missing.

## What the employee can do
Nothing on production pipelines: re-runs and backfills of production jobs are done by Data Platform once the cause is fixed (to avoid duplicate loads). Stale Tableau reports: add a banner to the dashboard to warn readers.

## Escalation
Data Platform owns Jenkins data jobs. Include: job name, first failing run, error text, failing stage, the related change IDs, and which downstream reports are stale.
