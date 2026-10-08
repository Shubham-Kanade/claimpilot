// @ts-check
/**
 * Static data of the mock: the demo personas, the company policy, the model routes, the calendar
 * and the "history" the statistics start from. Everything is synthetic and mirrors the real
 * sources named next to each table (the contract test checks the policy wording against
 * services/api/config/policy.yaml whenever that file exists).
 */

/** @typedef {import("./types.mjs").Employee} Employee */

// --- personas (services/mcp-corp/seed/employees.json) ------------------------------------------------

/**
 * ORDER MATTERS: the UI defaults to the first non-approver, so DEMO-ASHA comes first.
 * @type {Employee[]}
 */
export const EMPLOYEES = [
  {
    id: "DEMO-ASHA",
    name: "Asha Menon",
    employee_id: "EMP90001",
    grade: "L3",
    base_city: "Pune",
    base_state_code: "27",
  },
  {
    id: "P001",
    name: "Advika Hayer",
    employee_id: "EMP85968",
    grade: "L4",
    base_city: "Hyderabad",
    base_state_code: "36",
  },
  {
    id: "P005",
    name: "Advik Kaur",
    employee_id: "EMP35048",
    grade: "L4",
    base_city: "Panaji",
    base_state_code: "30",
  },
  {
    id: "DEMO-MEERA",
    name: "Meera Shah",
    employee_id: "EMP90003",
    grade: "L2",
    base_city: "Mumbai",
    base_state_code: "27",
  },
  {
    id: "DEMO-RAVI",
    name: "Ravi Iyer",
    employee_id: "EMP90002",
    grade: "L5",
    base_city: "Bengaluru",
    base_state_code: "29",
  },
];

/** Personas allowed to approve (Settings.approver_ids). */
export const APPROVER_IDS = ["DEMO-RAVI"];

// --- policy (services/api/config/policy.yaml) --------------------------------------------------------

/**
 * Clause id -> the exact wording users are shown when a finding cites the clause.
 * @type {Record<string, { title: string; text: string }>}
 */
export const POLICY_CLAUSES = {
  1.1: {
    title: "Scope",
    text: "This policy covers business expenses of every Orion Demo Corp employee in grades L1 to L5. All amounts are in Indian rupees (₹) and include GST unless a clause says otherwise.",
  },
  1.2: {
    title: "City tiers",
    text: "Cities are grouped into two tiers for expense limits. Tier-1 cities are Delhi/New Delhi, Mumbai, Bengaluru, Hyderabad, Chennai, Kolkata, Pune and Ahmedabad. Every other city is Tier-2.",
  },
  2.1: {
    title: "Submission window",
    text: "Claim an expense within 90 days of the day it was incurred. For a trip, the 90 days start on the last day of the trip. Claims submitted later are sent to finance for review.",
  },
  3.1: {
    title: "Receipts",
    text: "Every expense above ₹200 needs a receipt or tax invoice that shows the merchant, the date and the amount. For local conveyance and tips of ₹200 or less, a self-declaration (the route or purpose and the amount) can replace the receipt.",
  },
  4.1: {
    title: "Accommodation",
    text: "Hotel rooms are reimbursed up to a nightly limit that depends on the employee's grade and the city tier. The limit applies to the room rate before GST; other charges on the bill are not counted against it. Limits per night for Tier-1 / Tier-2 cities are L1 ₹4,500 / ₹4,200, L2 ₹6,000 / ₹5,500, L3 ₹8,000 / ₹7,500, L4 ₹9,500 / ₹8,800 and L5 ₹10,000 / ₹9,800. A night above the limit needs approval from finance.",
  },
  5.1: {
    title: "Meals",
    text: "Meals are reimbursed up to a daily limit per employee: ₹2,000 in Tier-1 cities and ₹1,500 in Tier-2 cities. The limit covers all of a day's meal bills, including GST and service charge. Client entertainment is covered by clause 5.2 instead.",
  },
  5.2: {
    title: "Client entertainment",
    text: "Entertaining clients is reimbursed up to ₹2,500 per head, after removing any alcohol (clause 6.1). The claim must name everyone who attended and give the business purpose.",
  },
  6.1: {
    title: "Alcohol",
    text: "Alcohol is never reimbursable. If a bill includes alcohol, it must be taken out of the claim.",
  },
  7.1: {
    title: "Air travel",
    text: "Air travel is booked in economy class only. A ticket in any other class needs approval from finance.",
  },
  7.2: {
    title: "Rail travel",
    text: "Rail travel is reimbursed up to the highest class allowed for the employee's grade: 3A (third AC) for L1 and L2, 2A (second AC) for L3 and L4, and 1A (first AC) for L5.",
  },
  7.3: {
    title: "Local travel on a trip",
    text: "Cabs, autos and other local travel at a trip destination are reimbursed up to ₹3,000 a day in Tier-1 cities and ₹2,500 a day in Tier-2 cities. Airport and station transfers count towards the daily total.",
  },
  8.1: {
    title: "Mobile and internet",
    text: "Mobile and internet bills are reimbursed up to a monthly limit by grade: ₹2,000 for L1 and L2, ₹2,500 for L3 and L4, and ₹3,000 for L5.",
  },
  9.1: {
    title: "Learning and certification",
    text: "A course, certification or similar learning expense above ₹10,000 needs your manager's approval before you buy it. Attach the approval to the claim.",
  },
  10.1: {
    title: "Personal expenses",
    text: "Personal expenses are not reimbursable, even when they are paid during a business trip.",
  },
};

/** The numbers the mock enforces (policy.yaml `params`). */
export const POLICY_PARAMS = {
  /** Clause 1.2: every other city is Tier-2. */
  tier1Cities: [
    "Delhi",
    "New Delhi",
    "Mumbai",
    "Bengaluru",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Ahmedabad",
  ],
  /** Clause 3.1: conveyance at or below this can be self-declared. */
  receiptThreshold: 200,
  /** Clause 4.1: nightly room-rate cap by grade and city tier (before GST). */
  nightlyCap: /** @type {Record<string, { tier1: number; tier2: number }>} */ ({
    L1: { tier1: 4500, tier2: 4200 },
    L2: { tier1: 6000, tier2: 5500 },
    L3: { tier1: 8000, tier2: 7500 },
    L4: { tier1: 9500, tier2: 8800 },
    L5: { tier1: 10000, tier2: 9800 },
  }),
  /** Clause 5.1: daily meals limit by city tier. */
  mealsDailyLimit: { tier1: 2000, tier2: 1500 },
  /** Clause 5.2: client entertainment per head, after removing any alcohol. */
  entertainmentPerHead: 2500,
  /** Clause 6.1 and 10.1: System One probability at or above which the rule fires. */
  alcoholThreshold: 0.5,
  personalThreshold: 0.5,
  /** Clause 8.1: monthly cap by grade. */
  mobileMonthlyCap: /** @type {Record<string, number>} */ ({
    L1: 2000,
    L2: 2000,
    L3: 2500,
    L4: 2500,
    L5: 3000,
  }),
  /** Clause 9.1: learning above this needs the manager's approval. */
  learningPreapproval: 10000,
  /** A claim at or below this, with nothing flagged and nothing open, needs no human review. */
  autoApproveLimit: 10_000,
};

// --- /v1/meta (services/api/config/models.yaml) --------------------------------------------------------

export const META = {
  llm_mode: "replay",
  decision_engine: "jev",
  /** The single-container hosted setup (one process, no Redis); `demo` comes from the options. */
  runtime: "embedded",
  /** In models.yaml order. */
  routes: [
    ["extraction", "haiku", "claude-haiku-5-5", "low"],
    ["extraction_retry", "sonnet", "claude-sonnet-5-5", "low"],
    ["agent_chat", "sonnet", "claude-sonnet-5-5", "low"],
    ["question_draft", "haiku", "claude-haiku-5-5", "low"],
    ["decision_fallback", "haiku", "claude-haiku-5-5", "low"],
    ["reply_parse", "haiku", "claude-haiku-5-5", "low"],
    ["locate", "haiku", "claude-haiku-5-5", "low"],
    ["policy_compile", "opus", "claude-opus-5-5", "high"],
    ["eval_judge", "sonnet", "claude-sonnet-5-5", "low"],
  ].map(([route, model_key, model_id, effort]) => ({
    route: String(route),
    model_key: String(model_key),
    model_id: String(model_id),
    effort: String(effort),
    overridden: false,
  })),
};

// --- calendar (services/mcp-corp/seed/calendar.json, the five demo personas) -----------------------

/**
 * @typedef {Object} CalendarEvent
 * @property {string} owner
 * @property {string} kind
 * @property {string} title
 * @property {string} date  ISO date.
 * @property {string[]} attendees
 */

/** @type {CalendarEvent[]} */
export const CALENDAR = [
  {
    owner: "DEMO-ASHA",
    kind: "client_meeting",
    title: "Quarterly review with Kestrel Logistics",
    date: "2026-10-06",
    attendees: ["Neha Rao (Kestrel Logistics)", "Rohan Kapoor (Kestrel Logistics)"],
  },
  {
    owner: "DEMO-ASHA",
    kind: "client_dinner",
    title: "Dinner with Kestrel Logistics",
    date: "2026-10-06",
    attendees: [
      "Neha Rao (Kestrel Logistics)",
      "Rohan Kapoor (Kestrel Logistics)",
      "Priya Nair (Kestrel Logistics)",
    ],
  },
  {
    owner: "DEMO-ASHA",
    kind: "travel",
    title: "Client visit to Mumbai",
    date: "2026-10-09",
    attendees: [],
  },
  {
    owner: "DEMO-MEERA",
    kind: "client_dinner",
    title: "Dinner with Harbour Analytics",
    date: "2026-10-08",
    attendees: ["Kavya Pillai (Harbour Analytics)", "Tarun Varma (Harbour Analytics)"],
  },
  {
    owner: "DEMO-RAVI",
    kind: "client_meeting",
    title: "Annual contract review with Blue Lotus Foods",
    date: "2026-10-07",
    attendees: ["Anita Desai (Blue Lotus Foods)", "Sameer Joshi (Blue Lotus Foods)"],
  },
  {
    owner: "P001",
    kind: "travel",
    title: "Business trip to Bengaluru",
    date: "2026-07-12",
    attendees: [],
  },
  {
    owner: "P005",
    kind: "client_dinner",
    title: "Client dinner with Kestrel Logistics",
    date: "2026-08-08",
    attendees: [
      "Karan Banerjee (Kestrel Logistics)",
      "Naveen Bhatt (Kestrel Logistics)",
      "Pooja Pillai (Kestrel Logistics)",
      "Anita Rao (Kestrel Logistics)",
    ],
  },
];

// --- statistics history -----------------------------------------------------------------------------

/**
 * What the platform did before this run, so the impact meter is never empty. The live numbers
 * (documents, claims, cost) are added on top of these.
 */
export const HISTORY = {
  documentsProcessed: 47,
  documentsFailed: 2,
  /** 9 earlier claims; their statuses add up to `claims`. */
  claims: 9,
  claimsByStatus: /** @type {Record<string, number>} */ ({
    approved: 5,
    rejected: 2,
    submitted: 1,
    ready: 1,
  }),
  autoApprovableClaims: 4,
  llmCostUsd: 0.0211,
  avgBatchSeconds: 9.4,
  /** How many batches `avgBatchSeconds` stands for when real batch durations are blended in. */
  batches: 5,
  assumedManualMinutesPerDocument: 4.0,
};

// --- limits (services/api/src/claimpilot/config.py) -----------------------------------------------------

export const LIMITS = {
  maxBatchFiles: 30,
  maxUploadBytes: 15 * 1024 * 1024,
};
