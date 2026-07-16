"use client";

import { useEffect } from "react";

import styles from "./workbench.module.css";

export default function CaseWorkbenchError({
  error,
  reset,
}: Readonly<{
  error: Error & { digest?: string };
  reset: () => void;
}>) {
  useEffect(() => {
    console.error("NetOps case workbench boundary failed", { digest: error.digest });
  }, [error.digest]);

  return (
    <section className={styles.workbench}>
      <div className={styles.errorState} role="alert">
        <p className={styles.eyebrow}>Case workbench boundary</p>
        <h1>The workbench stopped safely.</h1>
        <p>No case content is repeated here. Retry the request or share the server correlation ID with the platform team.</p>
        <button className="button button-primary" onClick={reset} type="button">Retry safely</button>
      </div>
    </section>
  );
}
