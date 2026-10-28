---
id: KB-101
title: "Okta: account locked after failed sign-ins"
systems: [Okta]
symptoms: [login_failed]
audience: employee
status: active
updated: 2026-10-14
---
## What happened
Okta locks an account after 5 failed sign-ins in a row. Since CHG-4405 (2026-10-13) a locked account stays locked until it is unlocked; it does not unlock itself after a timeout.

## Most common cause
After a password change, an app on another device keeps signing in with the OLD saved password (Outlook or Mail on a phone, a second laptop, a mapped drive). Each attempt counts as a failure and re-locks the account, sometimes minutes after an unlock.

## Fix (helpdesk agent may do this after identity verification)
1. Verify the employee's identity with a one-time code sent to their registered phone.
2. Unlock the account.
3. Before the employee signs in again, have them remove or update the saved password on every other device (for Outlook on iPhone: Settings > Accounts > the work account > Reset Account, or sign out and back in).
4. Ask the employee to sign in once in the browser and confirm it works.

## If the account locks again within minutes
Look at the sign-in log for the client that is failing. If no saved-password client explains it, escalate to Identity & Access (possible credential-stuffing attack).
