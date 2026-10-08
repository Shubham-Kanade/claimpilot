"use client";

import {
  useQueries,
  useQuery,
  type QueryKey,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

/*
 * Why these hooks exist: the server HTML of every screen is rendered with NO data (queries are
 * pending), so the first client render, the one that hydrates it, must see no data either.
 * TanStack Query hands out whatever is in its cache, and the cache may already hold an answer by
 * the time React gets to hydrate a later part of the page (React hydrates in pieces, and the
 * header's request is long finished by then). A component then renders "demo mode" or a list
 * where the server printed a skeleton: React error #418 and a page regenerated on the client.
 *
 * `useApiQuery` and `useApiQueries` answer "pending" while hydrating and the real result right
 * after; renders that are not hydration (navigating inside the app) get the real result at once.
 */

const subscribe = () => () => undefined;

/** False on the server and for the hydration render, true for every client render after that. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}

/** What a query that has not answered yet looks like (every field the UI could read). */
function pendingResult<TData, TError>(
  refetch: UseQueryResult<TData, TError>["refetch"],
): UseQueryResult<TData, TError> {
  return {
    data: undefined,
    error: null,
    status: "pending",
    fetchStatus: "fetching",
    isPending: true,
    isLoading: true,
    isInitialLoading: true,
    isFetching: true,
    isSuccess: false,
    isError: false,
    isLoadingError: false,
    isRefetchError: false,
    isRefetching: false,
    isPaused: false,
    isStale: true,
    isFetched: false,
    isFetchedAfterMount: false,
    isPlaceholderData: false,
    failureCount: 0,
    failureReason: null,
    errorUpdateCount: 0,
    dataUpdatedAt: 0,
    errorUpdatedAt: 0,
    refetch,
    // The union type of a real result cannot be built by hand: this one is only ever read as
    // "pending, no data, no error", which is exactly what the server printed.
  } as unknown as UseQueryResult<TData, TError>;
}

/** `useQuery` that is "pending" while the page hydrates, so it matches the server HTML. */
export function useApiQuery<
  TQueryFnData = unknown,
  TError = Error,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>): UseQueryResult<TData, TError> {
  const result = useQuery(options);
  const hydrated = useHydrated();
  return hydrated ? result : pendingResult<TData, TError>(result.refetch);
}

/** `useQueries` for a list of queries of one kind, with the same hydration rule. */
export function useApiQueries<TData>(
  queries: readonly UseQueryOptions<TData, Error, TData, QueryKey>[],
): UseQueryResult<TData, Error>[] {
  const results = useQueries({ queries: [...queries] });
  const hydrated = useHydrated();
  return hydrated ? results : results.map((result) => pendingResult<TData, Error>(result.refetch));
}
