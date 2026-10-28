---
id: KB-103
title: "Okta Verify: new or lost phone, MFA push not arriving"
systems: [Okta]
symptoms: [login_failed, mfa]
audience: employee
status: active
updated: 2026-08-18
---
## Push not arriving (same phone)
1. Open Okta Verify and pull down to refresh.
2. Check the phone has data or Wi-Fi and notifications for Okta Verify are on.
3. Use the 6-digit code in Okta Verify instead of the push.

## New phone or lost phone
Moving MFA to a new phone needs an MFA factor reset. Only the Identity & Access team can reset a factor, after a live identity check (video call with photo ID). The helpdesk agent must hand this off; it cannot reset MFA.
If the old phone still works, the employee can add the new phone themselves: Okta dashboard > Settings > Security Methods > Set up another.
If the phone was lost or stolen, also follow KB-161.
