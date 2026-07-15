"use client";

import { useEffect } from "react";

export default function RootError({
  error,
  reset,
}: Readonly<{
  error: Error & { digest?: string };
  reset: () => void;
}>) {
  useEffect(() => {
    // Deliberately avoid logging the error object: request content may be sensitive.
    console.error("NetOps Copilot UI boundary failed", { digest: error.digest });
  }, [error.digest]);

  return (
    <main className="error-page">
      <p className="eyebrow">APPLICATION BOUNDARY</p>
      <h1>We could not load this workspace.</h1>
      <p>
        No incident content is shown here. Retry the page or contact the platform
        team with the correlation ID from the API response.
      </p>
      <button className="button button-primary" onClick={reset} type="button">
        Retry safely
      </button>
    </main>
  );
}
