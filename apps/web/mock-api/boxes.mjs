// @ts-check
/**
 * Click-to-verify boxes: where each receipt field is printed on the sample images.
 *
 * TWEAK THE NUMBERS HERE. Every rectangle is `[x, y, w, h]` in PIXELS of the image in
 * mock-api/fixtures (so you can read them off any image viewer); `size` is that image's `[width,
 * height]`. They are turned into the contract's normalised boxes (fractions of the page, page 0)
 * by `boxesFor()`. Fields a document does not print are simply absent, and the other sample
 * documents have no boxes at all (the real backend often returns none, and the UI must cope).
 *
 * The values were measured on the images (text extents plus about 4 px of padding), so a box
 * hugs the printed value, not its label.
 */

/** @typedef {import("./types.mjs").Box} Box */

/**
 * @typedef {Object} BoxTable
 * @property {[number, number]} size  Image width and height in pixels.
 * @property {Record<string, [number, number, number, number]>} rects  Field -> `[x, y, w, h]`.
 */

/** @type {Record<string, BoxTable>} */
export const SAMPLE_BOXES_PX = {
  // hotel-folio-lotus-bay.png
  hotel: {
    size: [1100, 900],
    rects: {
      merchant_name: [394, 45, 312, 46],
      merchant_gstin: [498, 111, 160, 20],
      invoice_number: [800, 209, 86, 22], // Folio No.
      date: [800, 252, 112, 26], // Bill Date
      subtotal: [963, 561, 87, 22],
      total: [936, 661, 114, 24],
      line_items: [42, 403, 1015, 142], // the whole charges table, header included
    },
  },
  // cab-receipt-raahi-cabs.png
  cab: {
    size: [895, 1100],
    rects: {
      merchant_name: [68, 76, 197, 39],
      merchant_gstin: [438, 978, 191, 23],
      invoice_number: [185, 953, 151, 22],
      date: [72, 244, 137, 25],
      time: [213, 243, 65, 25],
      subtotal: [699, 605, 98, 25],
      total: [690, 730, 107, 28], // the "Total" row, not the headline amount at the top
      line_items: [98, 490, 699, 107], // the three fare lines
    },
  },
  // flight-ticket-suryoday-air.png
  flight: {
    size: [1100, 1026],
    rects: {
      merchant_name: [28, 28, 211, 43],
      merchant_gstin: [316, 933, 146, 22],
      invoice_number: [31, 140, 134, 37], // PNR / booking ref
      date: [484, 310, 117, 25], // travel date
      subtotal: [989, 743, 80, 23],
      total: [960, 823, 109, 25],
      line_items: [29, 555, 1041, 171], // the five fare lines
    },
  },
};

/**
 * @param {number} value
 */
function fraction(value) {
  return Math.round(value * 10_000) / 10_000;
}

/**
 * The normalised boxes of a sample (empty for samples without a table).
 * @param {string | null} key
 * @returns {Record<string, Box>}
 */
export function boxesFor(key) {
  const table = key ? SAMPLE_BOXES_PX[key] : undefined;
  if (!table) return {};
  const [width, height] = table.size;
  /** @type {Record<string, Box>} */
  const boxes = {};
  for (const [field, [x, y, w, h]] of Object.entries(table.rects)) {
    boxes[field] = {
      page: 0,
      x: fraction(x / width),
      y: fraction(y / height),
      w: fraction(w / width),
      h: fraction(h / height),
    };
  }
  return boxes;
}
