---
id: KB-131
title: "Snowflake access for data teams"
systems: [Snowflake]
symptoms: [missing_access]
audience: employee
status: active
updated: 2026-09-12
---
- **Snowflake DEV**: auto-approved for Data Engineering and Analytics. Use it while waiting for production access.
- **Snowflake PROD read-only**: manager + data owner (Data Governance) approval. 1-2 business days. New joiners must also have finished the "Handling Customer Data" training.
- **Snowflake PROD read/write**: not requestable for people; production writes run through service accounts.

Since CHG-4413 (2026-10-16) Snowflake PROD only accepts connections from allow-listed IP ranges (office networks, GlobalProtect, approved build subnets).
