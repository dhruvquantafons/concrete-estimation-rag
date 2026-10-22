# Data notes

How the source documents were read, what was checked, what was fixed, and what is still known to be wrong.
For the overview, see the [README](../README.md).

## What is in the database

| Document | What was extracted |
|---|---|
| IS 456 : 2000 (114 pages, old scan, re-OCR'd) | 455 clauses, 68 annex clauses, 28 tables, 21 amendment entries (Amendments 1–3) |
| IS 10262 : 2019 (digital PDF) | 81 clauses, 94 annex clauses (worked examples A–F), 15 tables |
| IS 1200 Parts 1, 2, 5, 8, 22 | 207 clauses (+ Part 8 Amendment 1) |
| IS 2386 Parts 1–8 + 2 amendment slips | 447 clauses, 15 tables, 29 amendment entries |
| CPWD DSR 2023 Volumes 1 and 2 (804 pages, 96-dpi scan, English and Hindi pages) | 2,955 items, 2,439 basic rates (labour, materials, hire), 346 cement-consumption coefficients |
| Search index | 6,927 chunks |

The review queue is empty. `config/corrections/cpwd_dsr_2023.yaml` holds about 770 checked fixes, which are
re-applied every time the DSR is ingested.

## How the scans are checked

- **Every DSR rate is read three times:** from the English page, by a digits-only re-read of the rate cell, and
  from the matching Hindi page. The three readings vote. About 95% of rates have at least two readings that agree.
  The rest go to the **Review queue** in the web app.
- **Item codes are decoded as a sequence.** OCR often loses dots ("414" for 4.1.4). Codes in a sub-head run
  almost in order, so the parser picks the most likely reading of the whole sequence. Uncertain codes are flagged.
- **Units are shown as printed** in the DSR (cum, sqm, 10 Nos, each set …). A leading count ("10 Nos",
  "100 metre") is never dropped.
- **Code tables used by the calculators** (`config/code_tables/`) are typed in from the standards, not taken
  from OCR. `crag verify-tables` cross-checks them against the OCR text, and each one has a sign-off.

## Fix history

### 2026-10-22

**Code tables re-checked, and AI checks no longer count as human sign-off.** Every curated value was compared
with the rendered page again, value by value. All match:

- IS 456: Tables 5, 6, 16, 18, 19 (78 values) and 20; cl. 26.2.1.1; the cl. 38.1 xu,max/d note; and the
  detailing limits.
- IS 10262 (digital PDF): Tables 1 to 5, newly checked.

Until now, a table signed `claude-vision` showed as **verified**, the status meant for a person's sign-off.
It now shows as **ai_checked** ("AI-checked, needs human sign-off"). Only a person's own name gives
**verified**. Two related fixes:

- The Code tables screen hid the sign-off button on AI-checked tables, so they could never be signed by a
  person. It now shows the button.
- The sign-off API refuses `claude-vision` as a name.

**IS 10262 Fig. 1 re-measured independently.** A second digitizer, with its own grid fit, measured all 51 stored
points. The largest difference was 0.28 N/mm² and the mean 0.10, inside the stated ±0.3, so nothing changed. The
image `data/processed/fig1_overlay.png` (git-ignored; it is part of the BIS figure) draws the stored points over
the figure, for checking against the printed copy.

**Equations readable.** The OCR of printed formulas in the scanned codes was unusable. For example,
cl. 26.2.1 read "L = 95s" for Ld = φ σs / (4 τbd), and cl. 39.3 read "PL = O4F,.A,+0.67F, A,". The fix:

- 46 equations, each read from its page, are now in `config/equations/`: 29 from IS 456 (cl. 6.2.2 to
  Annex G) and 17 IS 2386 test-result formulas.
- `crag ingest` adds each equation to its clause chunk, labelled with its check status. The OCR text stays, so
  the citation still matches the page.
- IS 10262 is a digital PDF, and its equations were already readable.

**Checked table values added to garbled table chunks.** The new answer tests showed the local model inventing
values for IS 456 Table 20 (3.2 instead of 3.5 for M30) and cl. 26.2.1.1 (6.0 instead of 1.4 for M25). The
OCR text of those tables was garbled ("1.5 1.8 2.0"), so the model had no correct numbers to read.

- On ingest, each curated code table's page-checked values are now added to its source chunk, labelled with
  their check status. That covers 11 chunks: IS 456 Tables 5, 16, 19, 20, cl. 26.2.1.1 and cl. 38.1, and
  IS 10262 Tables 1–5.
- The OCR text and the table JSON are unchanged.

**Two-column reading order fixed** (`ingest/layout.py`, `parse/is_code.py`). On IS 456 page 85,
cl. 39.7.1.1, 40, 40.1 and 40.1.1 had been merged into the chunk for cl. 40.2.1.1, and G-1.1 into G-1. All IS
codes were re-ingested and every changed chunk was compared with the previous version. Four causes were fixed:

- **A column's last word beside a table row.** When the last word of a left-column paragraph sat on the same
  baseline as a row of a right-column table, the gap across the gutter counted as a table gap. The line was then
  read as a full-width table row, which put the right column before the rest of the left. The gutter gap no
  longer counts as a table gap.
- **OCR comma in annex clause numbers.** "G-1,1" now reads as G-1.1, as main clause numbers already did.
- **"Label : value" rows across the page.** In the IS 10262 worked examples (A-1, B-1, B-2, D-1 …), rows like
  "Grade designation : M40" are now kept together. Before, the labels and values ended up in separate runs.
- **Full-width sentences on digital pages.** Where ordinary word spacing happens to sit on the gutter (IS 10262
  B-0, captions, the BIS watermark line), the line is no longer split. This applies to digital pages only.
  Scanned pages have column edges too ragged for this test: trying it there wrongly merged IS 456 cl. 26.5.1.2
  into 26.5.1.4, so it was dropped for scans.

Result: 45 clauses now have chunks of their own, where before they were inside a neighbour. In IS 456 they are
cl. 2, 3, 11.1, 11.2, 23.2, 28.2.2/3, 39.7.1.1, 40.1, 40.1.1, 41.3.2 to 41.4.2 and G-1.1. In IS 10262 they are
6.1.1 to 6.1.3 and the worked-example steps B-4/B-5, C-4 to C-6 and D-3 to F-6. The BIS watermark, which carries
the licence holder's name, no longer leaks into 8 IS 1200 chunks. The `attach_to` workaround was removed from
`config/equations/`, and all 46 equations now sit on their own clause.

### 2026-10-21

**Code tables reviewed.** Every IS 456 value used by the calculators was compared with the scanned pages, and all
matched: Tables 5, 16, 19 (all 78 values), 20 and 18 (load factor), cl. 26.2.1.1 bond stress, the cl. 38.1
xu,max/d note, and the detailing limits (cl. 26.3.3, 26.5.1.1, 26.5.1.5, 26.5.1.6, 26.5.2.1/2, 26.5.3.1, 23.2.1,
25.4, 39.3). They were signed off as `claude-vision`. The automatic check had flagged them only because the scan's
OCR misreads digits (for example Table 5 "Extreme 280" reads as 480).

**IS 10262 Fig. 1 digitized.** The figure is an image in the PDF (page 6). Its three curves were measured from the
pixels, accurate to about ±0.3 N/mm², and stored in `config/code_tables/is10262_2019.yaml`. Mix design now
picks the w/c ratio from it. The curve gives a slightly lower w/c than the standard's rounded worked examples
(0.35 against 0.36 for 48.25 N/mm² on curve 2). The IS 456 Table 5 limit still caps the result, and a target
strength outside the curve gives a warning. For PPC/PSC the calculator uses curve 2, as Fig. 1 Note 2 allows.

**Codes that OCR read as letters.** The scan sometimes prints the first number of a code as a letter
("n2" = 11.2, "a3" = 8.3, "TTT1" = 7771). The parser then glued that row onto the item above, and **that item
showed the next item's rate**. All three readings agreed, so it never reached the review queue. Fixed and checked
against the printed pages:

| Item | Was | Printed rate |
|---|---|---|
| 11.1.2 Brick-on-edge flooring, CM 1:6 | ₹792.05 | ₹965.55 |
| 8.2.3.2 Granite slab over 0.50 sqm | ₹298.60 | ₹3848.70 |
| 8.3.2 Granite edge moulding | ₹568.55 | ₹510.95 |
| 8.14.5.2 30 mm red sand stone | ₹714.40 | ₹3119.20 |
| Basic rate 7773 coloured paver block | ₹10.00 | ₹450.00 |

Recovered items that were missing: 11.2, 11.6.1, 11.7, 8.3.1, 8.4, 8.7.1, 8.15, 9.71.1 and basic rates 7771,
7774. "n" = 11 is repaired automatically. Unclear cases are listed under `code_tokens` at the top of
`config/corrections/cpwd_dsr_2023.yaml` (page + token → printed code). If you find another item whose rate
belongs to the next line, add an entry there and re-ingest.

**Parent descriptions.** A sub-item such as 5.22A.6 now carries its heading text, so search can match it.
Items without their heading text went from 314 to 209.

### 2026-10-20

- **Review queue cleared.** All 712 flagged rows were read against the scanned pages. 709 were fixed and tagged
  `checked_by: claude-vision`, and the rest were saved in the web app.
- **Units stored and shown as printed.** New units were added to `normalize/units.py` and the review dropdown:
  - measured quantities: `100m`, `cm`, `cm_per_m`, `m_depth`, `m3_m_depth`, `cm_depth_100m`,
    `cm_depth_cm_width_m`, `m_cm_girth`, `m_per_m_height`, `m_per_m_span`, `m3_per_m_span`,
    `letter_cm_height`, `dm3` (cudm), `10_dm3` (10 cudm)
  - counts and packs: `each_set`, `one_set`, `set`, `10_sets`, `pair`, `10_nos`, `100_nos`, `750_ml`, `1000_l`,
    `roll`, `bundle`, `cartridge`
  - per occurrence: `each_job`, `each_cut`, `each_hole`, `joint`, `wheel`, `test`
- **Count units fixed.** The parser used to store "10 Nos" and "100 Nos" as "each", so 122 basic rates looked 10×
  or 100× too high per piece ("1000 litre", "100 metre", "pair" and "set" were affected too). Bricks BR:1986 are
  per 1000 Nos (OCR read "74000 Nos").
- **Correction writer fixed.** Saving a row twice used to leave broken lines in the corrections file and drop
  earlier fixes. Each entry is now one line, and a re-save keeps the earlier fields.
- **Basic-rate codes can be corrected** in the review queue. OCR had misread 1543 as 1643 (flush pipe, p.48) and
  7692/7694 as 7682/7684 (K-9 flanged DI pipe, p.112). When OCR gives two rows the same code, the second one is
  stored as `BR:<code>#2`.
- **Unflagged code errors.** OCR sometimes splits a code wrongly while the rate and unit look fine. Seven were
  found and fixed: 18.83.6/7/9/11, 18.80.4, 18.93A.13, 11.11.1.
- **Calculator audit.** BOQ quantities, every DSR rate the estimator uses (4.1.8, 5.9.x, 5.22/5.22A, 5.33.x) and
  the IS 10262 mix design were checked against the printed pages and by hand. Three fixes:
  - Above-plinth steel is priced as 5.22A.6 instead of 5.22.6 (same rate, ₹107.85/kg).
  - Beam and slab deflection checks apply the IS 456 Fig. 4/5 modification factors. They used to assume the
    maximum factor (2.0), which let failing sections pass.
  - Footings under rectangular columns use the longer cantilever and the punching factor ks. The old code
    understated the moment by about 30% for a 230×600 column.

## Known issues

- **DSR rows that are still missing** (OCR merged or dropped them, so they are not in the database and never
  reach the review queue): 1.2.4–1.2.15, 1.2.16.3/.4, 3.1, 3.11, 4.9A (₹900.15), 5.4 (₹8821.65), 6.11 (₹608.15
  sqm), 8.21, 9.147.F1.1 (₹12496.05), 11.41A.2.1, 11.41A.2.3, 11.54.1, 15.15.1 (₹589.55 cum per metre span),
  15.16.1, 17.59.1, 18.95A.10, 19.2.2, 19.10.2, 19.15.1, 19.17.1, 19.22.1, 21.16.3, 24.4, and basic rates 2226
  (₹824.64) and 2228 (₹1413.67). The notes on the neighbouring rows in the corrections file give the printed rates.
- **209 sub-items lack their heading text,** so search only sees their short tail (for example "1:6 (1 cement :
  6 coarse sand)"). Rates are correct. To fix one, add a `description` to its correction.
- **Item codes on unflagged rows** have not been checked systematically. `18.5.0.1` (p.142, probably 18.50.1) is
  unconfirmed.
- **Cement-consumption coefficients for sub-head 11** (Volume 2, p.342) are poorly read ("11.1.2 = 612.85",
  skirting under code "16"). The BOQ only uses coefficients for concrete items (sub-heads 4 and 5), so estimates
  are not affected.
- **Percentage items have no ₹ rate on purpose:** 2.24.1 (+20%), 2.24.2 (+25% per metre depth) and 5.9.14 (+20% of
  the shuttering item). A BOQ line using one shows ₹0 with a warning; add that extra by hand.
- **BOQ over-measurement:** slab soffit shuttering does not deduct the strip resting on beams (about 1–2% of
  formwork), and column height is taken floor to floor instead of to the slab soffit.

## How to fix data

- **In the web app:** Review queue → pick the row → correct code, unit or rate → Save. The fix is written to
  `config/corrections/cpwd_dsr_2023.yaml` and applied at once.
- **By hand:** add or edit a line in that file, then re-ingest the DSR (OCR is cached, so it takes a minute or two)
  and rebuild search, with `crag serve` stopped:
  `crag ingest --doc CPWD_DSR_2023_V1 --doc CPWD_DSR_2023_V2 && crag index`
- To undo a correction, delete its line and re-ingest.
