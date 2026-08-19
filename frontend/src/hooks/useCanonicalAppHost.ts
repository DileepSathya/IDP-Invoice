import { useEffect } from "react";

/** Portable build: keep session cookies on one host (launcher opens 127.0.0.1:8000). */
export function useCanonicalAppHost() {
  useEffect(() => {
    const { hostname, port, pathname, search, hash } = window.location;
    if (hostname === "localhost" && port === "8000") {
      window.location.replace(`http://127.0.0.1:8000${pathname}${search}${hash}`);
    }
  }, []);
}
