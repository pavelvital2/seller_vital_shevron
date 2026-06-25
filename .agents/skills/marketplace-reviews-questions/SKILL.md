---
name: marketplace-reviews-questions
description: "Use for Ozon/Wildberries reviews and questions workflows: read-only collection, buyer ratings, media inspection, draft replies, approval packages, applying approved replies, marking viewed items, verify and cleanup."
---

# Marketplace Reviews And Questions

## Core Rules

- Read `AGENTS.md`, `data/planning/reviews_questions_runbook.md`, and
  `data/planning/chat_report_templates.md`.
- Replies to buyers and marking reviews viewed are write operations.
- Use the full chain:
  `read-only -> dry-run -> review -> approved -> apply -> verify -> cleanup`.
- Do not produce identical boilerplate replies for every review.
- A review with photo/video but no text still needs a public reply when the
  marketplace allows it.
- Show the buyer rating on every proposed reply row.
- Inspect attached media when links/previews are available; if media cannot be
  inspected, state that limitation.
- Send review media that affects reply approval to the owner in Telegram.
- For WB reviews, media can be present directly in Feedbacks API fields such as
  `photoLinks` and `video`/`videos`/`videoLinks`; treat these as media evidence
  and send/download them for owner approval when present.

## Source Routes

- WB: official Feedbacks/Questions API first.
- Ozon: Seller Review/Question API first; if unavailable or 403, use the
  documented LK/CDP fallback from the runbook.
- For Ozon review media details use
  `scripts/reviews/ozon_review_media_detail_cdp.js` when the list source only
  contains media counts.

## Output

Use the saved reviews/questions templates:

1. сколько и каких отзывов/вопросов;
2. оценки;
3. что написали и что отметили медиа;
4. чем недовольны;
5. предложенные ответы;
6. что требует owner approval;
7. apply/verify result after approval.

After a successful operation, update `reviews_questions_runbook.md` with any
new confirmed recovery path or marketplace limitation.
