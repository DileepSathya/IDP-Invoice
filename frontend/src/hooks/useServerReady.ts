import { useEffect, useState } from "react";
import { fetchHealth } from "../api";

const DEFAULT_POLL_MS = 2000;
const DEFAULT_MAX_ATTEMPTS = 90;

export function useServerReady(pollMs = DEFAULT_POLL_MS, maxAttempts = DEFAULT_MAX_ATTEMPTS) {
  const [ready, setReady] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const wait = async () => {
      for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        if (cancelled) return;
        try {
          if (await fetchHealth()) {
            if (!cancelled) {
              setReady(true);
              setTimedOut(false);
            }
            return;
          }
        } catch {
          // API still starting — keep polling.
        }
        await new Promise((resolve) => window.setTimeout(resolve, pollMs));
      }
      if (!cancelled) {
        setTimedOut(true);
      }
    };

    void wait();
    return () => {
      cancelled = true;
    };
  }, [pollMs, maxAttempts]);

  return { ready, timedOut };
}
