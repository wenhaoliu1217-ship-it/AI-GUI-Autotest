# Security Policy

## Test Authorization

Only run AI-GUI against systems and accounts you are authorized to test. A natural-language scenario never grants permission for payment, refund, deletion, publishing, invitation, production submission, or other high-risk actions.

## Public Repository Hygiene

Do not commit:

- passwords, API keys, tokens, cookies, or authorization headers;
- Playwright storageState files or DPAPI-encrypted session files;
- internal URLs, customer data, private screenshots, traces, or reports;
- `.env` files, virtual environments, runtime data, logs, or caches.

Use environment-variable references for runtime secrets. Examples and tests must contain synthetic values only.

## Reporting

Report security issues privately to the repository maintainers. Do not include live credentials, customer data, or exploitable internal endpoints in public issues.
