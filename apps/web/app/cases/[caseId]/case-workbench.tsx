"use client";

import { useActionState, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type {
  ArtifactStatusResponse,
  CaseDetailResponse,
  CaseState,
} from "../../../../../packages/api-client/src/generated";

import {
  idleCaseActionResult,
  requestFeedbackAction,
  resolveCaseAction,
  transitionCaseAction,
  type CaseActionResult,
} from "./actions";
import styles from "./workbench.module.css";
import { AudioIntake } from "./audio-intake";
import { ConfigPreview } from "./config-preview";

const stateRail: readonly CaseState[] = [
  "new",
  "investigating",
  "needs_information",
  "diagnosed",
  "fix_proposed",
  "confirmed",
  "resolved",
  "learned",
];

const primaryStateRail = stateRail.filter((state) => state !== "needs_information");

const transitionTargets: Readonly<Record<CaseState, readonly CaseState[]>> = {
  new: ["investigating"],
  investigating: ["diagnosed"],
  diagnosed: ["fix_proposed"],
  fix_proposed: ["confirmed"],
  needs_information: ["investigating"],
  confirmed: [],
  resolved: [],
  learned: [],
};

function stateLabel(state: CaseState): string {
  if (state === "needs_information") return "pending customer answer";
  return state.replaceAll("_", " ");
}

function eventLabel(eventType: string, toState: CaseState | null): string {
  if (toState !== null) return `Moved to ${stateLabel(toState)}`;
  return eventType.replaceAll(".", " ").replaceAll("_", " ");
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recorded time unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

function shortIdentifier(value: string | null): string {
  if (value === null) return "Organization-wide";
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function artifactKindLabel(value: string): string {
  return value === "network-configuration" ? "Configuration" : "Incident audio";
}

function artifactStatusLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function stateRailClass(state: CaseState, current: CaseState): string | undefined {
  if (state === current) return styles.current;
  if (state === "needs_information") return undefined;

  const currentIndex = current === "needs_information"
    ? primaryStateRail.indexOf("investigating")
    : primaryStateRail.indexOf(current);
  return primaryStateRail.indexOf(state) < currentIndex ? styles.complete : undefined;
}

function ActionMessage({ result }: Readonly<{ result: CaseActionResult }>) {
  const router = useRouter();
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (result.status === "success") router.refresh();
    if (result.status === "error" || result.status === "conflict") resultRef.current?.focus();
  }, [result.status, router]);

  if (result.status === "idle") return null;

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className={styles.actionResult}
      data-status={result.status}
      ref={resultRef}
      role={result.status === "error" || result.status === "conflict" ? "alert" : "status"}
      tabIndex={-1}
    >
      <p>{result.message}</p>
      {result.status === "conflict" ? (
        <button onClick={() => router.refresh()} type="button">
          Refresh case
        </button>
      ) : null}
    </div>
  );
}

function TransitionForm({ caseId, state, version }: Readonly<{ caseId: string; state: CaseState; version: number }>) {
  const targets = transitionTargets[state];
  const [result, action, pending] = useActionState(
    transitionCaseAction.bind(null, caseId),
    idleCaseActionResult,
  );

  if (targets.length === 0) return null;
  const requiresApproval = targets.includes("confirmed");

  return (
    <form action={action} className={styles.actionForm}>
      <input name="expected_version" type="hidden" value={version} />
      <label>
        Advance case
        <select aria-label="Next case state" defaultValue={targets[0]} name="to_state" required>
          {targets.map((target) => (
            <option key={target} value={target}>
              {stateLabel(target)}
            </option>
          ))}
        </select>
      </label>
      {requiresApproval ? (
        <label>
          Approval record ID
          <input
            aria-describedby="approval-help"
            name="approval_id"
            placeholder="Immutable approval UUID"
            required
            type="text"
          />
          <span className={styles.formHelp} id="approval-help">
            Confirmation is only valid with the approved record.
          </span>
        </label>
      ) : null}
      <label>
        Operator note <span className={styles.formHelp}>(optional)</span>
        <textarea name="note" placeholder="Record the handoff context, not unverified conclusions." />
      </label>
      <ActionMessage result={result} />
      <button disabled={pending} type="submit">
        {pending ? "Recording…" : "Record transition"}
      </button>
    </form>
  );
}

function ResolutionForm({ caseId, version }: Readonly<{ caseId: string; version: number }>) {
  const [result, action, pending] = useActionState(
    resolveCaseAction.bind(null, caseId),
    idleCaseActionResult,
  );

  return (
    <form action={action} className={styles.actionForm}>
      <input name="expected_version" type="hidden" value={version} />
      <label>
        Verification note
        <textarea
          name="verification_note"
          placeholder="What did you verify, and how?"
          required
        />
      </label>
      <ActionMessage result={result} />
      <button disabled={pending} type="submit">
        {pending ? "Recording…" : "Resolve with verification"}
      </button>
    </form>
  );
}

function FeedbackForm({ caseId, version }: Readonly<{ caseId: string; version: number }>) {
  const [result, action, pending] = useActionState(
    requestFeedbackAction.bind(null, caseId),
    idleCaseActionResult,
  );

  return (
    <form action={action} className={styles.actionForm}>
      <input name="expected_version" type="hidden" value={version} />
      <label>
        Question for customer
        <textarea
          name="note"
          placeholder="Example: Please confirm the public IP and internal host for the inbound NAT request."
          required
        />
      </label>
      <ActionMessage result={result} />
      <button disabled={pending} type="submit">
        {pending ? "Recording…" : "Mark pending customer answer"}
      </button>
    </form>
  );
}

function CaseActions({
  detail,
  canWriteCases,
}: Readonly<{ detail: CaseDetailResponse; canWriteCases: boolean }>) {
  const { case: caseRecord } = detail;
  const isTerminal = caseRecord.state === "learned";

  return (
    <section aria-labelledby="case-actions" className={`${styles.panel} ${styles.actionPanel}`}>
      <h2 className={styles.panelTitle} id="case-actions">Controlled actions</h2>
      <p className={styles.actionIntro}>
        Each action carries version {caseRecord.version}. A changed case will reject a stale write.
      </p>
      {!canWriteCases ? (
        <p className={styles.terminalNotice}>
          Your verified role is read-only. The immutable timeline remains available, but case changes are not offered.
        </p>
      ) : null}
      {canWriteCases ? (
        <>
      {caseRecord.state === "confirmed" ? (
        <ResolutionForm caseId={caseRecord.id} version={caseRecord.version} />
      ) : null}
      {caseRecord.state === "investigating" ? (
        <FeedbackForm caseId={caseRecord.id} version={caseRecord.version} />
      ) : null}
      <TransitionForm caseId={caseRecord.id} state={caseRecord.state} version={caseRecord.version} />
      {isTerminal || caseRecord.state === "resolved" ? (
        <p className={styles.terminalNotice}>
          {isTerminal
            ? "This case is learned. Further state changes are intentionally unavailable to operators."
            : "Resolution is recorded. Learning is performed by the verified indexing workflow, not the browser."}
        </p>
      ) : null}
        </>
      ) : null}
    </section>
  );
}

export function CaseWorkbench({
  detail,
  canWriteCases,
  artifactStatuses,
}: Readonly<{
  detail: CaseDetailResponse;
  canWriteCases: boolean;
  artifactStatuses: readonly ArtifactStatusResponse[];
}>) {
  const { case: caseRecord, timeline } = detail;

  return (
    <section aria-labelledby="case-title" className={styles.workbench}>
      <Link className={styles.returnLink} href="/cases">← Back to case queue</Link>
      <header className={styles.masthead}>
        <div>
          <p className={styles.eyebrow}>Case / version {caseRecord.version}</p>
          <h1 className={styles.title} id="case-title">{caseRecord.title}</h1>
        </div>
        <dl className={styles.caseMeta}>
          <div>
            <dt>State</dt>
            <dd>{stateLabel(caseRecord.state)}</dd>
          </div>
          <div>
            <dt>Severity</dt>
            <dd className={styles.severity} data-severity={caseRecord.severity}>{caseRecord.severity}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{formatTimestamp(caseRecord.updated_at)}</dd>
          </div>
        </dl>
      </header>

      <div className={styles.grid}>
        <section aria-labelledby="state-rail" className={styles.panel}>
          <h2 className={styles.panelTitle} id="state-rail">State rail</h2>
          <ol className={styles.stateRail}>
            {stateRail.map((state) => (
              <li
                aria-current={state === caseRecord.state ? "step" : undefined}
                className={stateRailClass(state, caseRecord.state)}
                key={state}
              >
                {stateLabel(state)}
              </li>
            ))}
          </ol>
          {caseRecord.state === "needs_information" ? (
            <p className={styles.stateCaption}>Pending customer answer. The exact question is preserved in the immutable activity record; continue investigation when the reply arrives.</p>
          ) : null}
        </section>

        <section aria-labelledby="case-activity" className={styles.panel}>
          <h2 className={styles.panelTitle} id="case-activity">Immutable activity</h2>
          {timeline.length === 0 ? (
            <p className={styles.emptyTimeline}>No timeline facts are visible for this case yet.</p>
          ) : (
            <ol aria-label="Case activity in recorded order" className={styles.timeline}>
              {timeline.map((entry) => (
                <li className={styles.timelineItem} key={entry.event_id}>
                  <time className={styles.timelineTime} dateTime={entry.occurred_at}>
                    {formatTimestamp(entry.occurred_at)}
                  </time>
                  <div className={styles.eventBody}>
                    <p className={styles.eventTitle}>{eventLabel(entry.event_type, entry.to_state)}</p>
                    {entry.note !== null ? <p className={styles.eventDetail}>{entry.note}</p> : null}
                    {entry.verification_note !== null ? <p className={styles.eventDetail}>Verified: {entry.verification_note}</p> : null}
                    <span className={styles.eventFact}>Version {entry.aggregate_version} · Event {shortIdentifier(entry.event_id)}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        <CaseActions canWriteCases={canWriteCases} detail={detail} />

        {canWriteCases ? <ConfigPreview caseId={caseRecord.id} /> : null}
        {canWriteCases ? <AudioIntake caseId={caseRecord.id} /> : null}

        <section aria-labelledby="case-facts" className={styles.panel}>
          <h2 className={styles.panelTitle} id="case-facts">Case record</h2>
          <dl className={styles.facts}>
            <div><dt>Case ID</dt><dd>{caseRecord.id}</dd></div>
            <div><dt>Category</dt><dd>{caseRecord.category ?? "Unclassified"}</dd></div>
            <div><dt>Asset scope</dt><dd title={caseRecord.asset_id ?? undefined}>{shortIdentifier(caseRecord.asset_id)}</dd></div>
            <div><dt>Opened</dt><dd>{formatTimestamp(caseRecord.created_at)}</dd></div>
          </dl>
          <div className={styles.evidencePlaceholder}>
            <strong>Evidence routing</strong>
            {artifactStatuses.length === 0 ? (
              <p>No secure evidence has been completed for this case yet.</p>
            ) : (
              <ul aria-label="Evidence processing status" className={styles.evidenceStatusList}>
                {artifactStatuses.map((artifact) => (
                  <li key={artifact.artifact_id}>
                    <span>{artifactKindLabel(artifact.artifact_kind)}</span>
                    <span>{artifactStatusLabel(artifact.status)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}
