# Dispatch fixtures

Response samples for the channel tests in `tests/test_dispatch_*.py`. Nothing here was recorded from a
live workspace (no real Slack or Resend calls are made during development); the bodies follow the
providers' documented responses:

- `slack_ok.txt`: Slack incoming webhooks answer `200` with the body `ok`.
- `slack_no_service.txt`: a revoked or mistyped webhook answers `404` with `no_service`.
- `slack_invalid_payload.txt`: a malformed body answers `400` with `invalid_payload`.
- `resend_ok.json`: `POST https://api.resend.com/emails` answers `200` with the email id.
- `resend_validation_error.json`: Resend's `422` error body.
