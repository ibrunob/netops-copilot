"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import type { CreateCaseActionResult } from "./actions";
import styles from "./new-case.module.css";

function newIdempotencyKey(): string {
  return globalThis.crypto.randomUUID();
}

export function NewCaseIntake({ initialError }: { initialError?: string }) {
  const router = useRouter();
  const [result, setResult] = useState<CreateCaseActionResult>(() =>
    initialError === undefined ? { status: "idle", message: "" } : { status: "error", message: initialError },
  );
  const [pending, setPending] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const resultRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (result.status === "success" && result.caseId !== undefined) {
      router.replace(`/cases/${result.caseId}`);
    }
    if (result.status === "error") resultRef.current?.focus();
  }, [result, router]);

  function rotateKeyForChangedIntent() {
    if (hasSubmitted) {
      setIdempotencyKey(newIdempotencyKey());
      setHasSubmitted(false);
    }
  }

  async function submitCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setHasSubmitted(true);
    setPending(true);
    try {
      const response = await fetch("/api/cases", {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new FormData(event.currentTarget),
      });
      const payload = (await response.json()) as CreateCaseActionResult;
      setResult(payload);
    } catch {
      setResult({
        status: "error",
        message: "The case could not reach the local application. Keep this form unchanged and retry safely.",
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <section className={`content-frame ${styles.intake}`} aria-labelledby="new-case-title">
      <Link className={styles.returnLink} href="/cases">← Back to case queue</Link>
      <header className={styles.masthead}>
        <div>
          <p className="eyebrow">OPERATIONS / CASE INTAKE</p>
          <h1 id="new-case-title">Open a case</h1>
        </div>
        <p className={styles.headerNote}>
          The authenticated API derives your organization and actor. This browser never chooses either.
        </p>
      </header>

      <form
        action="/api/cases"
        className={styles.form}
        method="post"
        onChange={rotateKeyForChangedIntent}
        onSubmit={submitCase}
      >
        <input name="idempotency_key" type="hidden" value={idempotencyKey} />
        <section className={styles.requiredPanel} aria-labelledby="case-facts-title">
          <div className={styles.sectionHeading}>
            <p className={styles.sectionIndex}>01</p>
            <div>
              <h2 id="case-facts-title">Triage facts</h2>
              <p>Record only what can be attributed to the current incident.</p>
            </div>
          </div>
          <div className={styles.fieldGrid}>
            <label className={styles.titleField}>
              Case title
              <input autoFocus maxLength={500} name="title" required type="text" />
            </label>
            <label>
              Severity
              <select defaultValue="high" name="severity" required>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </label>
            <label>
              Category <span>optional</span>
              <input maxLength={100} name="category" type="text" />
            </label>
            <label>
              Asset ID <span>optional UUID</span>
              <input
                aria-describedby="asset-scope-help"
                name="asset_id"
                placeholder="Organization-wide when blank"
                type="text"
              />
            </label>
          </div>
          <p className={styles.helpText} id="asset-scope-help">
            Asset scope is checked by the API against your signed-in permissions. A blank asset is organization-wide.
          </p>
        </section>

        <section className={styles.evidencePanel} aria-labelledby="evidence-title">
          <div className={styles.sectionHeading}>
            <p className={styles.sectionIndex}>02</p>
            <div>
              <h2 id="evidence-title">Immutable input</h2>
              <p>Optional structured evidence is saved with the original case intent.</p>
            </div>
          </div>
          <div className={styles.evidenceGrid}>
            <label>
              Input kind
              <input
                aria-describedby="input-help"
                maxLength={100}
                name="input_kind"
                placeholder="e.g. syslog, alert, operator-note"
                type="text"
              />
            </label>
            <label>
              JSON object
              <textarea
                aria-describedby="input-help"
                name="input_content"
                placeholder='{ "key": "value" }'
                spellCheck="false"
              />
            </label>
          </div>
          <p className={styles.helpText} id="input-help">
            Supply both fields together. Content must be a JSON object and cannot be edited after this case is accepted.
          </p>
        </section>

        <footer className={styles.submitBar}>
          <div>
            <p className={styles.submitTitle}>Submit once, recover safely.</p>
            <p className={styles.submitCopy}>
              Retries keep the same idempotency key until you change the intended case, preventing duplicate cases after a connection failure.
            </p>
          </div>
          <button disabled={pending} type="submit">
            {pending ? "Submitting…" : "Create case"}
          </button>
        </footer>
        {result.status !== "idle" ? (
          <p
            aria-live="polite"
            aria-atomic="true"
            className={styles.actionResult}
            data-status={result.status}
            ref={resultRef}
            role={result.status === "error" ? "alert" : "status"}
            tabIndex={-1}
          >
            {result.message}
          </p>
        ) : null}
      </form>
    </section>
  );
}
