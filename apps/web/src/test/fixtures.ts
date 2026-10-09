/**
 * Typed builders for API objects, built on the GENERATED types so a contract change breaks the
 * tests at compile time. Values mirror the synthetic scenarios of the mock API.
 */
import type {
  BatchView,
  ClaimView,
  Decisions,
  DocumentView,
  Employee,
  ExtractedReceipt,
  Finding,
  LlmOps,
  MetaInfo,
  OpenQuestion,
  ProcessedDocument,
  Stats,
} from "@/lib/api/types";

export const ASHA: Employee = {
  id: "DEMO-ASHA",
  name: "Asha Menon",
  employee_id: "EMP90001",
  grade: "L3",
  base_city: "Pune",
  base_state_code: "27",
};

export const ADVIKA: Employee = {
  id: "P001",
  name: "Advika Hayer",
  employee_id: "EMP85968",
  grade: "L4",
  base_city: "Hyderabad",
  base_state_code: "36",
};

export const RAVI: Employee = {
  id: "DEMO-RAVI",
  name: "Ravi Iyer",
  employee_id: "EMP90002",
  grade: "L5",
  base_city: "Bengaluru",
  base_state_code: "29",
};

export const EMPLOYEES: Employee[] = [ASHA, ADVIKA, RAVI];

export function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    code: "total_mismatch",
    severity: "high",
    message: "The printed total ₹11,146.28 doesn't match the bill's own items, taxes and charges.",
    fields: ["total"],
    source: "trust",
    ...overrides,
  };
}

export const HOTEL_CAP_FINDING: Finding = makeFinding({
  code: "hotel_over_cap",
  severity: "high",
  source: "policy",
  message: "Hotel night ₹7,700.00 exceeds the ₹7,500.00 cap for grade L3 in a Tier-2 city.",
  fields: ["line_items"],
  expected: 7500,
  actual: 7700,
  clause_id: "4.1",
  clause_text:
    "Hotel rooms are reimbursed up to a nightly limit that depends on the employee's grade and the city tier.",
});

export function makeReceipt(overrides: Partial<ExtractedReceipt> = {}): ExtractedReceipt {
  return {
    doc_type: "hotel_folio",
    merchant_name: "Lotus Bay Suites",
    merchant_gstin: "04ZPWCN7743D8ZE",
    merchant_city: "Chandigarh",
    invoice_number: "F2626239",
    date: "2026-08-18",
    time: null,
    currency: "INR",
    line_items: [
      { description: "Room Charges 15-Aug", amount: 7700 },
      { description: "Room Charges 16-Aug", amount: 7200 },
      { description: "Room Charges 17-Aug", amount: 7200 },
    ],
    subtotal: 21600,
    taxes: { cgst: 540, sgst: 540, igst: null, cess: null, gst_rate_percent: 5 },
    service_charge: null,
    discount: null,
    total: 22680,
    payment_method: "card",
    upi_reference: null,
    travel_from: null,
    travel_to: null,
    languages: ["en"],
    handwritten: false,
    low_confidence_fields: [],
    contains_instructions: false,
    ...overrides,
  };
}

export function makeDecisions(overrides: Partial<Decisions> = {}): Decisions {
  return {
    category: "accommodation",
    category_confidence: 0.99,
    alcohol_present: 0.01,
    personal_expense: 0.02,
    engine: "jev",
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<ProcessedDocument> = {}): ProcessedDocument {
  return {
    id: "doc-000001",
    filename: "hotel-folio-lotus-bay.png",
    sha256: "a".repeat(64),
    receipt: makeReceipt(),
    decisions: makeDecisions(),
    findings: [],
    boxes: {},
    ...overrides,
  };
}

export function makeDocumentView(overrides: Partial<DocumentView> = {}): DocumentView {
  const document = overrides.document === undefined ? makeDocument() : overrides.document;
  return {
    id: document?.id ?? "doc-000001",
    filename: document?.filename ?? "hotel-folio-lotus-bay.png",
    position: 0,
    status: "processed",
    error: null,
    document,
    trust_score: 100,
    verdict: "clean",
    ...overrides,
  };
}

export function makeQuestion(overrides: Partial<OpenQuestion> = {}): OpenQuestion {
  return {
    id: "q-business_purpose-1a2b3c4d",
    kind: "business_purpose",
    text: "What was the business purpose of the Chandigarh trip 15–18 Aug 2026?",
    document_ids: ["doc-000001"],
    answer: null,
    ...overrides,
  };
}

export function makeClaim(overrides: Partial<ClaimView> = {}): ClaimView {
  return {
    id: "clm-DEMO-ASHA-1a2b3c4d5e",
    employee_id: "DEMO-ASHA",
    title: "Chandigarh trip 15–18 Aug 2026",
    mode: "trip",
    status: "ready",
    document_ids: ["doc-000001"],
    total: 22680,
    currency: "INR",
    start_date: "2026-08-15",
    end_date: "2026-08-18",
    city: "Chandigarh",
    findings: [],
    open_questions: [],
    submission_reference: null,
    batch_id: "bat-000001",
    route: "finance_review",
    ...overrides,
  };
}

export function makeBatchView(overrides: Partial<BatchView> = {}): BatchView {
  return {
    id: "bat-000001",
    employee_id: "DEMO-ASHA",
    status: "processing",
    total: 2,
    processed: 0,
    failed: 0,
    created_at: "2026-10-08T10:00:00Z",
    finished_at: null,
    error: null,
    documents: [
      { id: "doc-000001", filename: "cab.png", position: 0, status: "queued" },
      { id: "doc-000002", filename: "hotel.png", position: 1, status: "queued" },
    ],
    claims: [],
    ...overrides,
  };
}

export const META: MetaInfo = {
  llm_mode: "replay",
  llm_record: false,
  daily_llm_budget_usd: 1,
  max_batch_files: 30,
  max_upload_mb: 15,
  decision_engine: "jev",
  demo: false,
  runtime: "distributed",
  routes: [
    {
      route: "extraction",
      model_key: "haiku",
      model_id: "claude-haiku-5-5",
      effort: "low",
      overridden: false,
    },
    {
      route: "agent_chat",
      model_key: "sonnet",
      model_id: "claude-sonnet-5-5",
      effort: "low",
      overridden: false,
    },
    {
      route: "decision_fallback",
      model_key: "haiku",
      model_id: "claude-haiku-5-5",
      effort: null,
      overridden: true,
    },
  ],
};

/** The hosted public demo: replayed recordings, "Start over" available. */
export const META_DEMO: MetaInfo = { ...META, demo: true, runtime: "embedded" };

export function makeOps(overrides: Partial<LlmOps> = {}): LlmOps {
  return {
    hours: 24,
    since: "2026-10-08T12:00:00",
    sampled: false,
    totals: {
      calls: 200,
      live_calls: 50,
      recorded_calls: 150,
      errors: 3,
      not_recorded: 2,
      budget_refusals: 1,
      error_rate: 0.015,
      live_cost_usd: 0.1234,
      recorded_cost_usd: 0.5678,
      input_tokens: 360000,
      output_tokens: 48000,
      cache_read_tokens: 151200,
      cache_read_share: 0.42,
    },
    routes: [
      {
        route: "extraction",
        model_key: "haiku",
        calls: 120,
        live_calls: 30,
        errors: 2,
        p50_ms: 2400,
        p95_ms: 5200,
        live_cost_usd: 0.09,
        recorded_cost_usd: 0.4,
      },
      {
        route: "agent_chat",
        model_key: "sonnet",
        calls: 80,
        live_calls: 20,
        errors: 0,
        p50_ms: 850,
        p95_ms: 1700,
        live_cost_usd: 0.0334,
        recorded_cost_usd: 0.1678,
      },
    ],
    failures: [],
    trace_calls: [],
    ...overrides,
  };
}

export function makeStats(overrides: Partial<Stats> = {}): Stats {
  return {
    documents_processed: 47,
    documents_failed: 2,
    claims: 9,
    claims_by_status: { ready: 3, submitted: 4, approved: 2 },
    auto_approvable_claims: 3,
    llm_calls: 49,
    llm_cost_usd: 0.0211,
    llm_cost_per_document_usd: 0.00045,
    avg_batch_seconds: 9.4,
    assumed_manual_minutes_per_document: 4,
    estimated_minutes_saved: 188,
    ...overrides,
  };
}
