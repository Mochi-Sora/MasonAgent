# Dates

Dated commitments, deadlines, and events. Kept out of memory on purpose: the
date is the point, and long-term memory curates toward timeless facts. The agent
reads this file on demand with the `search_dates` tool.

Write one entry per line, ISO date first, so the date is unambiguous:

- 2026-10-05 — physics test
- 2026-11-01 — renew domain
- 2026-12-24 — flight to Lisbon

Lines without a date are still searchable by keyword. Headings and blanks are
ignored. Keeping entries short makes them easier to scan.
