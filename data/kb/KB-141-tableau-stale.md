---
id: KB-141
title: "Tableau report shows old data"
systems: [Tableau]
symptoms: [job_failure, other]
audience: employee
status: active
updated: 2026-06-15
---
Check "Data last refreshed" in the dashboard header. If the extract refresh failed, the cause is usually the upstream pipeline (see KB-140). Viewers cannot trigger refreshes; report it to the data source owner or Data Platform.
