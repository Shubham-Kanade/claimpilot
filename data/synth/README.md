# Synthetic Indian receipts (`data/synth`)

Generates ClaimPilot's evaluation dataset: realistic Indian expense documents with exact ground
truth. The principle is **truth first, then render**. Each document starts as a
`claimpilot.domain.ReceiptTruth`, and the image or PDF is rendered from it. So every label is
exact, and `truth.receipt` (an `ExtractedReceipt`) describes only what is printed.

All merchants, brands, apps, banks and people are fictional. GSTINs have valid checksums but random
PAN parts. Nothing here is real data.

## Commands

```bash
cd data/synth
uv sync
uv run generate.py all --count 100 --seed 42          # ~2.5 min, deterministic
uv run generate.py all --count 20 --seed 7 --only restaurant_bill,upi_payment
uv run generate.py fixtures                           # refresh fixtures/ from out/
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

| Subcommand | Does |
|---|---|
| `scenarios` | Builds personas and "a month of life", then writes `out/specs/` and `out/truth/` (starts a fresh dataset) |
| `render` | Renders specs to `out/clean/` as PNG (plus PDF for `gst_invoice` and `mobile_bill`) with Playwright |
| `degrade` | Turns clean renders into `out/docs/` (scans, phone photos, faded thermal) and writes the manifest |
| `adversarial` | Regenerates the adversarial documents on top of the scenario documents, then renders and degrades them |
| `all` | Runs every stage. `--count` is the total; about 20% of it is adversarial |
| `fixtures` | Copies a small subset of `out/` into the committed `fixtures/` folder |

Flags:
- `--count N`, `--seed S` and `--out PATH` (default `out/`).
- `--only a,b` takes `DocType` values. The aliases `thermal_bill` and `upi` also work.
- `--browser auto|chromium|msedge|chrome`.

The same arguments and seed give identical truths and byte-identical images. PDFs differ only in
their embedded timestamp. Bump `DATASET_VERSION` in `generate.py` whenever builders, templates or
degradations change; `out/dataset.json` records it.

## How it works

```text
personas.py   Faker en_IN persona: name, grade L1-L5, base city, month
scenarios.py  month of life: 1-2 trips (flight/train + hotel + cabs + meals, shared trip_id),
              client dinners, local cabs, auto/kirana bills, UPI payments, mobile bill,
              course or WFH purchase, fuel
builders/     one builder per doc type: ExtractedReceipt + render-only extras (address, PNR ...)
gst.py        GST rules + money rounding + reconciliation check
assemble.py   Draft -> DocSpec: id, languages (Devanagari detection), tags, degradation plan
render.py     Jinja2 templates/ -> HTML -> Playwright screenshot / PDF
degrade.py    Augraphy paper/ink effects + OpenCV perspective warp onto a desk, light, noise
adversarial.py, manifest.py, fixtures.py
```

**Document types (10).** The thermal and handwritten styles are what make them hard to read.
- `restaurant_bill`: 58/80 mm thermal roll, monospace.
- `gst_invoice`: A4 PDF.
- `hotel_folio`, `cab_receipt`, `flight_ticket`, `train_ticket`.
- `fuel_slip`: 58 mm thermal.
- `mobile_bill`: PDF.
- `upi_payment`: phone screenshot from a fictional app.
- `handwritten_bill`: a kirana cash memo or an auto fare slip, in Kalam/Caveat handwriting and often in Hindi.

**GST rules.**
- Restaurant, cab, economy flight and AC train: 5%.
- Hotel: 5% up to Rs 7,500 a night (GST 2.0, Sept 2025), 18% above.
- Telecom, courses and goods: 18%.
- Fuel, auto, kirana and UPI: no GST.

When the supplier's state differs from the place of supply, the tax is IGST; otherwise it is CGST
plus SGST. The rates live in `gst.py`, so they are easy to update. Note that GST 2.0 (Sept 2025)
cut the hotel rate for rooms up to Rs 7,500 to 5%; this dataset follows the project spec.

For every document that is not tampered: line items + taxes + service charge − discount = total,
to 2 decimal places.

**Labelling conventions** (the extraction prompt should follow these):
- For tickets, `date` and `time` are the journey's departure, and `invoice_number` is the PNR. The booking date is also printed.
- A hotel folio's `date` is the checkout (bill) date.
- `travel_from` and `travel_to` are city names. They are empty for local rides (cabs and autos), which print only localities.
- A fuel slip has a single line item: quantity is litres and `unit_price` is rate per litre. Preset fills mean quantity × rate only roughly equals the amount.
- `languages` is `["en"]`, or `["en", "hi"]` when any Devanagari is printed.

**Degradation presets** (chosen per document at scenario time):
- PDFs stay clean.
- UPI screenshots are clean or re-shared (`screen_capture`).
- Paper documents are `clean` 20% of the time; otherwise `scan`, `photo`, `photo_folded`, `photo_low_light` or `thermal_faded`.
- About 30% of the dataset ends up clean.
- Photos are stored as JPEG, like a phone would. The page is never cropped.

**Adversarial tags:**
- `duplicate`: the same receipt re-photographed, with `duplicate_of` set.
- `tampered`: an edited total, inflated GST or an inflated line, so it deliberately does not reconcile. The truth records what is printed.
- `injection`: a printed "NOTE TO AI SYSTEM ..." line, with `contains_instructions=true`.
- `over_policy`: alcohol on a dinner bill, or a hotel night above Rs 10,000.
- `missing_date`: no date printed, so `date` is null.

AI-generated images are added by hand to `ai_generated/`.

## Output (`out/`, gitignored)

```text
docs/<id>.png|jpg|pdf   final documents (PDFs also have docs/<id>.preview.png)
truth/<id>.json         ReceiptTruth
manifest.jsonl          {id, path, truth_path, doc_type, tags, split}; paths relative to out/
dataset.json            version, seed, counts per doc type / tag / split, wall time, size
specs/, html/, clean/   intermediate files (re-render or debug a single document)
```

`split` is `dev` for 20 documents. They are stratified across doc types, include injection,
tampered, over-policy and duplicate cases, and keep each duplicate in the same split as its
original. Everything else is `test`. **Never tune prompts on `test`.**

`fixtures/` (committed, about 1.2 MB) holds 10 documents for API tests, with the same manifest
format. Its images are downscaled palette PNGs.

## Rendering and fonts

Playwright needs a Chromium-family browser. With `--browser auto`, the generator tries:
1. Playwright's bundled Chromium (`uv run playwright install chromium`).
2. The installed Microsoft Edge (`channel="msedge"`).
3. Google Chrome.

The corporate proxy blocks the Chromium download, so on managed laptops the generator uses Edge.
Rendering tests skip themselves when no browser can be launched.

The fonts in `fonts/` (woff2: latin, latin-ext and devanagari subsets) come from the `@fontsource`
npm packages, version 5.3.0. They are Kalam, Caveat, Courier Prime, Inter, Noto Sans, Noto Sans
Devanagari and Noto Sans Mono. All are under the SIL Open Font License; each folder has its
`OFL.txt`. `fonts/fonts.css` declares the `@font-face` rules that the templates load.
