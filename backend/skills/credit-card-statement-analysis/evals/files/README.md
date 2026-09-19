# Test statement images

Three consecutive monthly statements (page 2/3) from the same 玉山銀行 Pi card, ending
8223. Renamed from their original camera-roll filenames so the eval prompts read clearly.

| File | Period | 本期新增款項 | What it exercises |
|---|---|---|---|
| `statement-2026-06.jpg` | June | NT$5,420 | A travel-heavy month: four SUICA top-ups, five 國外交易服務費 rows, mostly mobile payments. Opening balance + one bill payment to exclude. |
| `statement-2026-07.jpg` | July | NT$3,360 | Mixed month with insurance, a gateway-prefixed merchant (藍新－普洛ＰＵＲＯ), a row dated after the period end, and *two* bill payments. |
| `statement-2026-08.jpg` | August | NT$5,728 | Domestic month dominated by one 樂天市場 purchase; tests that a single large row doesn't crowd out the rest. |

All three are clear scans, so they test correctness rather than OCR robustness. The
`ANTHROPIC* CLAUDE SUB` charge appears in all three at roughly USD 20 — that is the
subscription `compare_statements.py` should surface.

`../ground_truth/` holds a hand-checked transcription of each, verified against the
printed 本期合計. Use it to grade extraction without re-reading the images by eye.

The year is not printed on these pages; 2026 is inferred, consistently, as the skill
instructs.

**Redact before adding more.** Black out full card numbers, account numbers, and names —
the skill only needs the last four digits, and these files go into git.
