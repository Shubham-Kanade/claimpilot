import { vi } from "vitest";

/** A stand-in for next/navigation in unit tests (no App Router is mounted in jsdom). */
export const router = {
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
  refresh: vi.fn(),
};

export const navigationState = { pathname: "/" };

export const navigationMock = {
  useRouter: () => router,
  usePathname: () => navigationState.pathname,
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
};
