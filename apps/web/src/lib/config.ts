/**
 * Where "see the README" links point (the public repository's README). Defined once so no URL is
 * hard-coded in the UI; override at build time with NEXT_PUBLIC_README_URL.
 */
export const README_URL =
  process.env.NEXT_PUBLIC_README_URL ?? "https://github.com/Shubham-Kanade/claimpilot#readme";
