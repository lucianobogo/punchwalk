"""Sending punch items to trades (PLAN 6.1-6.2).

- `message.py`: the message as the trade sees it (heading, one line per item, share links; plain text,
  Slack mrkdwn and Block Kit), identical in the preview and in what is sent.
- `slack.py`, `email.py`, `outbox.py`: one channel each, same `send(message, ...)` shape.
- `service.py`: channel availability and choice, preview, and `send_items` (Dispatch row + item
  status + history).
- `policy.py`: the automatic send after processing, and re-sending still-open items after a re-walk.
"""
