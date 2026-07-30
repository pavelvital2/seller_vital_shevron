---
name: marketplace-ozon-messenger
description: "Use for Ozon Messenger and notification workflows: buyer chats, unread messages, platform notifications, important message triage, reply drafts, approved sending, mark-read, verify and tail cleanup."
---

# Marketplace Ozon Messenger

## Core Rules

- Read `AGENTS.md`, `data/planning/ozon_messenger_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Treat buyer replies and marking notifications read as write operations.
- Use `/ozon-inbox` as the owner-facing Telegram route for Ozon reviews,
  questions, Messenger buyer chats and platform notifications. The callback
  `ozin_apply:<run_id>` is approval only for the exact saved inbox package.
- Daily Messenger work must not stop at reporting: after approval, send
  approved replies, mark approved notifications read, verify, and save
  non-secret closed/tail state so old items do not reappear as new tasks.
- When Messenger is applied through `/ozon-inbox`, close approval tails after
  successful apply and zero-action verify: original inbox pending, linked
  reviews/questions pending, and fresh verify pending must not remain in
  `pending_review`.
- Short closing customer replies after a seller answer, such as "Спасибо, но
  нет" or "Отказ", should be owner-approved as `mark_chat_read` without sending
  a new message. If official `/v2/chat/read` returns Premium Plus 403 for a
  Customer chat, use the documented LK/CDP open-chat fallback and verify
  `/v3/chat/history` shows no unread Customer message.
- Separate:
  - buyer questions needing replies;
  - important Ozon marketplace messages to forward to Telegram;
  - noise/promotional banners;
  - old read customer-tail dialogs.
- Customer wording such as `на заказ делаются`, `под заказ`, `изготовления
  нету` and `не печатаете` must follow the confirmed Vital Shevron rule:
  individual custom manufacture is unavailable and only ready variants from
  the store are offered. Keep the reply natural for the exact question.
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
- If the approved send helper finds the textarea but click is intercepted by
  the Ozon modal `Загрузите документы на бренд`, do not click
  `Загрузить документы`. With owner approval, click `Больше не показывать`
  once and verify on a new page that the modal is absent. Without that
  approval, the helper may hide only this exact known overlay locally for the
  current page; this must not change account settings or approved message text.
- Marketplace notifications can be marked read through `/v2/chat/read` when the
  runbook confirms `chat_id` and `from_message_id`.
- `/ozon-inbox` must paginate `/v3/chat/list` by `cursor` up to the working
  daily limit, not inspect only the first page. Treat `total_unread_count`
  as a weak signal: report it, but rely on row `unread_count` plus
  `/v3/chat/history` unread messages for actions.
- If an approved `/ozon-inbox` package contains only `mark_chat_read` actions
  and no `send_chat_message` actions, do not run the LK/CDP send helper.
  Treat send as `skipped` and decide success from `/v2/chat/read` results plus
  verify.
- If an owner-approved customer gratitude/no-question item must be closed
  without a reply and `/v2/chat/read` returns Premium Plus `403`, use the
  documented LK/CDP fallback to open the target chat in the Vital Shevron
  contour without sending a message, then verify through `/v3/chat/history`
  that no `Customer is_read=false` remains.
- If the newest unread Customer message contains only a photo/file and the
  immediately preceding unread Customer message contains the actual question,
  build one reply action from both messages: use the previous text for draft
  classification, retain the media evidence in the owner report, and target
  the newest message id so the entire unread tail closes after apply.

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
