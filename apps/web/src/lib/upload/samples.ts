/**
 * The "Try with sample receipts" pack: Asha Menon's week, 15 synthetic documents that tell one
 * story (data/synth/demo/README.md), shipped in /public/samples so anyone can try ClaimPilot
 * without their own files.
 *
 * The files are copied BYTE-IDENTICALLY from data/synth/demo/docs and must stay that way: the
 * hosted demo replays recorded model answers keyed by the sha256 of each file, so a re-encoded
 * photo is a different document and would not replay (samples.test.ts compares the bytes).
 *
 * Order matters twice: it is the order of the cards on the live-processing screen, and the second
 * copy of a duplicated bill is the one that gets flagged, so the pile is uploaded in this order.
 */
export interface SampleReceipt {
  /** File under /public/samples; it is also the name the upload gets. */
  file: string;
  type: string;
  /** What it is, in a few words (the "What it is" column of the pile's README). */
  label: string;
}

const JPEG = "image/jpeg";
const PNG = "image/png";
const PDF = "application/pdf";

export const SAMPLE_RECEIPTS: readonly SampleReceipt[] = [
  {
    file: "01-client-dinner-saffron-terrace.jpg",
    type: JPEG,
    label: "Restaurant bill, Pune, 6 Oct (phone photo)",
  },
  {
    file: "02-train-pune-to-mumbai.jpg",
    type: JPEG,
    label: "Rail e-ticket, Pune to Mumbai (scanned printout)",
  },
  {
    file: "03-cab-mumbai-station-to-hotel.png",
    type: PNG,
    label: "Cab e-receipt, Mumbai, station to hotel",
  },
  {
    file: "04-dinner-mumbai-9-oct.jpg",
    type: JPEG,
    label: "Dinner bill, Mumbai, 9 Oct (low light)",
  },
  {
    file: "05-hotel-folio-mumbai.pdf",
    type: PDF,
    label: "Hotel folio, one night in Mumbai (PDF)",
  },
  {
    file: "06-train-mumbai-to-pune.jpg",
    type: JPEG,
    label: "Rail e-ticket, Mumbai to Pune (phone photo)",
  },
  {
    file: "07-cab-pune-to-kestrel-office.jpg",
    type: JPEG,
    label: "Cab e-receipt, Pune, to the client's office",
  },
  {
    file: "08-cab-pune-hinjewadi-to-kothrud.png",
    type: PNG,
    label: "Cab e-receipt, Pune, Hinjewadi to Kothrud",
  },
  {
    file: "09-auto-slip-pune.jpg",
    type: JPEG,
    label: "Handwritten auto-rickshaw slip, in Hindi",
  },
  {
    file: "10-mobile-bill-october.pdf",
    type: PDF,
    label: "Postpaid mobile bill (PDF)",
  },
  {
    file: "11-upi-payment-120.png",
    type: PNG,
    label: "UPI payment screenshot, Rs 120, no note",
  },
  {
    file: "12-client-dinner-saffron-terrace-copy.jpg",
    type: JPEG,
    label: "The same restaurant bill, photographed again",
  },
  {
    file: "13-cab-pune-shivajinagar-to-baner.jpg",
    type: JPEG,
    label: "Cab e-receipt, Pune, 8 Oct (the total was edited)",
  },
  {
    file: "14-cafe-bill-banyan-pune.jpg",
    type: JPEG,
    label: "Cafe bill with a note to the AI reviewer",
  },
  {
    file: "15-dinner-mumbai-10-oct.jpg",
    type: JPEG,
    label: "Dinner bill, Mumbai, 10 Oct (with alcohol)",
  },
];

export const SAMPLES_BASE_PATH = "/samples";

/** Whose receipts the pile is (the recordings include her calendar), so the demo acts as her. */
export const SAMPLE_PERSONA_ID = "DEMO-ASHA";

/** Fetch every sample from /public/samples and turn it into a File ready to upload. */
export async function loadSampleFiles(
  samples: readonly SampleReceipt[] = SAMPLE_RECEIPTS,
  fetcher: typeof fetch = (input, init) => fetch(input, init),
): Promise<File[]> {
  const files: File[] = [];
  for (const sample of samples) {
    const response = await fetcher(`${SAMPLES_BASE_PATH}/${sample.file}`);
    if (!response.ok) throw new Error(`Could not load sample ${sample.file} (${response.status})`);
    // The bytes go through untouched (never re-encoded): the file is just a labelled copy.
    files.push(new File([await response.arrayBuffer()], sample.file, { type: sample.type }));
  }
  return files;
}
