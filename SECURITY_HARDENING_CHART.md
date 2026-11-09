# Security hardening reference chart

Scope: local/classroom use, with logging improvements as the first milestone.
This is a prioritized work chart, not a claim that the listed controls are
already implemented.

| Priority | Area | Proposed work | Why it matters | Verification |
|---|---|---|---|---|
| 1 | Audit trail | Define and test event schemas; assign server-generated request IDs and log a request event for every completed HTTP response. Correlate domain decision, appeal, and rejection events when available. | Connects related events and makes failures or suspicious request patterns diagnosable. | See [AUDIT_EVENT_SCHEMA.md](./AUDIT_EVENT_SCHEMA.md); run `python -m unittest test_audit test_app_audit`; confirm each response ID matches its JSONL event, including 4xx and 5xx paths. |
| 1 | Audit data privacy | Log submitted text and appeal reasoning only up to 4,000 characters; mark truncated values. Use `0700` directory and `0600` file permissions on POSIX. Rotate at 5 MiB and retain three backups. | Text and appeal explanations may contain personal or confidential information; bounded rotation limits exposure and disk use. | `python -m unittest test_audit`; confirms truncation, permissions, rotation, and clearing retained files. |
| 1 | Log access | `/log` is loopback-only and returns at most 100 entries. It does not trust forwarded IP headers. | The endpoint exposes user submissions and service activity. | `python -m unittest test_app_audit`; confirms non-loopback denial, bad-limit rejection, and result cap. |
| 2 | Input validation | Require an expected JSON object and validate field types, required values, and text length before model inference. Add a request-body size cap. | Malformed or oversized inputs can cause errors, consume resources, or create noisy logs. | Test missing/null/wrong-type fields, malformed JSON, empty text, and oversized bodies; expect clear client errors and rejection events. |
| 2 | Rate limiting | `/submit` is limited to 10/minute and 200/day per client address using in-memory storage. `creator_id` is caller-controlled and not used as a key. | Limits constrain repeated submissions; address-based keys cannot be bypassed by changing the body ID but can aggregate multiple people behind one address. | `python -m unittest test_app_audit`; sends requests with different `creator_id` values and confirms the 429 and correlated rejection event. |
| 2 | Error handling | Return generic client-facing errors while recording a request ID and useful internal reason; avoid exposing stack traces or model internals. | Detailed errors can leak implementation details and complicate client behavior. | Trigger expected failures and confirm responses do not contain traces or local paths while logs retain diagnostic context. |
| 3 | Deployment | Debug is opt-in and loopback-only; `python app.py` refuses non-loopback binds. Before network exposure, use a WSGI server behind TLS/authentication, deny `/log` at the proxy, and replace in-memory rate-limit storage for multiple workers. | Debug interfaces and development servers are unsafe for network-facing service; proxying localhost can bypass the app-level loopback check for `/log`. | Confirm non-loopback development startup exits, debug is off, proxy denies `/log`, and the tested production stack uses suitable persistent limits. |
| 3 | Regression tests | Add focused tests for validation, size limits, rate-limit behavior, event shapes, log bounds, and failure handling. Review dependency updates periodically. | Security controls can regress silently as routes or dependencies change. | Run the focused suite and dependency checks as part of routine changes. |

## Suggested order

1. Establish audit logger tests and define the event schema.
2. Add request correlation and complete event coverage.
3. Configure bounded content logging, local file permissions, rotation, and
   loopback-only log access.
4. Enable and test address-based rate limiting on `/submit`.
5. Apply deployment controls before any non-local exposure.

## Current implementation notes

- [app.py](./app.py) has `/log`, `/health`, and request routes. The `/submit`
  rate-limit decorator is active when rate limiting is enabled in config.
- [audit.py](./audit.py) writes JSONL decision, appeal, and rejection events.
- [config.py](./config.py) sets the 10/minute, 200/day limits, 5 MiB rotation,
  three backups, 4,000-character content cap, and 100-entry `/log` cap.
- `/log` denies non-loopback clients. Do not change this policy without adding
  authentication and tests suitable for the deployment. A local reverse proxy
  must deny `/log`, since its app connection may appear loopback.
- Do not expose this service or its log endpoint publicly until access control,
  production serving, and the full checklist in [RUNNING.md](./RUNNING.md) are
  in place.
