# Extraction bake-off: bakeoff-dev-haiku

- Date: 2026-10-08 · split: `dev` · receipts: 20 · prompt: `extract_v1` · total spend: $0.0929

| Config | Critical acc | Field acc | Line-item F1 | JSON valid | Injection recall | $/receipt | $/1k | p50 ms | p95 ms | Gates | Pareto |
|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-low | 100.0% | 95.0% | 0.95 | 100% | 100% | $0.00042 | $0.42 | 3106 | 3890 | ✅ | ★ |
| cascade | 100.0% | 96.9% | 0.95 | 100% | 100% | $0.00422 | $4.22 | 3300 | 7490 | ✅ |  |

**Chosen (cheapest frontier config passing gates):** haiku-low

- `haiku-low` top errors: [('subtotal', 2), ('doc_type', 2), ('travel_from', 2), ('travel_to', 2), ('igst', 1)]; failures: 0
- `cascade` top errors: [('doc_type', 1), ('payment_method', 1), ('igst', 1), ('upi_reference', 1), ('travel_from', 1)]; failures: 0
