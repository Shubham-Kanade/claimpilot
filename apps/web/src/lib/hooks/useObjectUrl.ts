"use client";

import { useEffect, useState } from "react";

/**
 * A `blob:` URL for a Blob/File (for <img src> and <object data>), revoked when the blob changes
 * or the component unmounts so previews never leak memory. Returns null until the URL exists.
 * (Needed because the API's file endpoint requires a header, so images cannot point at it.)
 */
export function useObjectUrl(blob: Blob | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) return;
    const objectUrl = URL.createObjectURL(blob);
    // Creating a blob URL is a side effect that can only happen after mount; the cleanup below
    // revokes it. This is the one legitimate "set state from an effect" in the app.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUrl(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
      setUrl(null);
    };
  }, [blob]);
  return url;
}
