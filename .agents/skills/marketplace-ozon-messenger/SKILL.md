---
name: marketplace-ozon-messenger
description: "Use for Ozon Messenger and notification workflows: buyer chats, unread messages, platform notifications, important message triage, reply drafts, approved sending, mark-read, verify and tail cleanup."
---

# Marketplace Ozon Messenger

## Core Rules

- Read `AGENTS.md`, `data/planning/ozon_messenger_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Treat buyer replies and marking notifications read as write operations.
- Daily Messenger work must not stop at reporting: after approval, send
  approved replies, mark approved notifications read, verify, and save
  non-secret closed/tail state so old items do not reappear as new tasks.
- Separate:
  - buyer questions needing replies;
  - important Ozon marketplace messages to forward to Telegram;
  - noise/promotional banners;
  - old read customer-tail dialogs.
- Never save raw buyer personal data, cookies, storage state, auth headers, or
  raw chat dumps into committed docs.

## Source Routes

- Prefer official Seller API `/v3/chat/list` and `/v3/chat/history` for
  read-only chat reports.
- Use LK/CDP websocket fallback only when API is unavailable or insufficient.
- Before using LK/CDP fallback, verify the project contour: cwd/project,
  CDP port, browser `user-data-dir`, and expected store must match Vital
  Shevron. Vital Shevron Ozon uses `127.0.0.1:9544` and
  `.sessions/ozon/chrome-profile`; do not use another project's CDP/profile.
- If official send API returns Premium Plus `403`, use the documented helper
  `scripts/messenger/ozon_send_messages_cdp.js` only for approved messages and
  verify through `/v3/chat/history`.
- If the helper returns `message_input_not_found`, do not retry blindly. Run
  the redacted Messenger probe and check whether the LK page shows websocket
  auth failure `ws1006`. A general Ozon keepalive/status `ok` does not prove
  Messenger websocket health.
- Marketplace notifications can be marked read through `/v2/chat/read` when the
  runbook confirms `chat_id` and `from_message_id`.

## Output

Owner-facing reports must show:

1. buyer replies needed;
2. important platform messages;
3. noise filtered out;
4. proposed answers/actions;
5. approval package;
6. apply/verify/cleanup result.

After a successful or recovered operation, update
`ozon_messenger_runbook.md`.
