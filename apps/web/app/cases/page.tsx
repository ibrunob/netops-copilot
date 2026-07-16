import Link from "next/link";
import type { Route } from "next";

import type { CaseResponse, CaseState } from "../../../../packages/api-client/src/generated";
import { listTenantCases, isCaseQueueApiError } from "@/lib/api/cases";
import { canWriteCases, getAuthenticatedSession } from "@/lib/auth/session";

import styles from "./cases.module.css";
import { QueueEvents } from "./queue-events";

export const metadata = {
  title: "Cases",
};

type SearchParameters = Record<string, string | string[] | undefined>;

type QueueFilters = Readonly<{
  query: string;
  state: CaseState | "";
  severity: CaseResponse["severity"] | "";
  cursor: string;
}>;

const knownStates = new Set<CaseState>([
  "new",
  "investigating",
  "diagnosed",
  "fix_proposed",
  "needs_information",
  "confirmed",
  "resolved",
  "learned",
]);

const knownSeverities = new Set<CaseResponse["severity"]>([
  "low",
  "medium",
  "high",
  "critical",
]);

function parameter(parameters: SearchParameters, name: string): string {
  const value = parameters[name];
  return typeof value === "string" ? value.trim().slice(0, 100) : "";
}

function filtersFrom(parameters: SearchParameters): QueueFilters {
  const state = parameter(parameters, "state");
  const severity = parameter(parameters, "severity");

  return {
    query: parameter(parameters, "q"),
    state: knownStates.has(state as CaseState) ? (state as CaseState) : "",
    severity: knownSeverities.has(severity) ? severity : "",
    cursor: parameter(parameters, "cursor").slice(0, 512),
  };
}

function displayState(state: CaseState): string {
  if (state === "needs_information") return "pending customer answer";
  return state.replaceAll("_", " ");
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
}

function queueHref(filters: QueueFilters, cursor?: string): Route {
  const parameters = new URLSearchParams();
  if (filters.query !== "") parameters.set("q", filters.query);
  if (filters.state !== "") parameters.set("state", filters.state);
  if (filters.severity !== "") parameters.set("severity", filters.severity);
  if (cursor !== undefined && cursor !== "") parameters.set("cursor", cursor);
  const query = parameters.toString();
  return (query === "" ? "/cases" : `/cases?${query}`) as Route;
}

function CaseFilters({ filters }: Readonly<{ filters: QueueFilters }>) {
  return (
    <form className={styles.controls} action="/cases" method="get" role="search">
      <label className={styles.field}>
        Search real cases
        <input
          defaultValue={filters.query}
          name="q"
          placeholder="Title, category, or case ID"
          type="search"
        />
      </label>
      <label className={styles.field}>
        State
        <select defaultValue={filters.state} name="state">
          <option value="">All states</option>
          {[...knownStates].map((state) => (
            <option key={state} value={state}>
              {displayState(state)}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        Severity
        <select defaultValue={filters.severity} name="severity">
          <option value="">All severities</option>
          {[...knownSeverities].map((severity) => (
            <option key={severity} value={severity}>
              {severity}
            </option>
          ))}
        </select>
      </label>
      <div className={styles.filterActions}>
        <button type="submit">Apply</button>
        <Link className={styles.clearLink} href="/cases">
          Clear
        </Link>
      </div>
    </form>
  );
}

function QueueError({ status }: Readonly<{ status?: number }>) {
  const explanation =
    status === 401 || status === 403
      ? "Your verified session cannot read this organization’s case queue. Sign in again or ask an administrator to check your role."
      : "The queue could not be read from the NetOps API. No cached or invented incidents are displayed.";

  return (
    <section className={styles.error} aria-labelledby="queue-error-title" role="alert">
      <p aria-hidden="true" className={styles.stateIndex}>
        !
      </p>
      <div>
        <p className="eyebrow">QUEUE UNAVAILABLE{status === undefined ? "" : ` / ${status}`}</p>
        <h2 id="queue-error-title">Live case data is unavailable.</h2>
        <p>{explanation}</p>
      </div>
    </section>
  );
}

function EmptyQueue({ filtered }: Readonly<{ filtered: boolean }>) {
  return (
    <section className={styles.empty} aria-labelledby="queue-empty-title">
      <p aria-hidden="true" className={styles.stateIndex}>
        00
      </p>
      <div>
        <p className="eyebrow">{filtered ? "NO MATCHES" : "QUEUE CLEAR"}</p>
        <h2 id="queue-empty-title">
          {filtered ? "No cases match this operational view." : "No cases are currently visible to your role."}
        </h2>
        <p>
          {filtered
            ? "Adjust the durable URL filters above or clear them to return to the organization queue."
            : "Create a case when there is a triage record to capture. This screen never fills an empty queue with invented incidents."}
        </p>
      </div>
    </section>
  );
}

function CaseTable({ cases }: Readonly<{ cases: readonly CaseResponse[] }>) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col" style={{ width: "34%" }}>
              Case
            </th>
            <th scope="col" style={{ width: "14%" }}>
              Severity
            </th>
            <th scope="col" style={{ width: "17%" }}>
              State
            </th>
            <th scope="col" style={{ width: "16%" }}>
              Asset
            </th>
            <th scope="col" style={{ width: "19%" }}>
              Updated (UTC)
            </th>
          </tr>
        </thead>
        <tbody>
          {cases.map((caseItem) => (
            <tr key={caseItem.id}>
              <td>
                <Link className={styles.caseLink} href={`/cases/${caseItem.id}` as Route}>
                  <span>{caseItem.title}</span>
                  <span className={styles.caseId}>{caseItem.id}</span>
                </Link>
              </td>
              <td>
                <span className={styles.severity} data-severity={caseItem.severity}>
                  {caseItem.severity}
                </span>
              </td>
              <td>
                <span className={styles.state}>{displayState(caseItem.state)}</span>
              </td>
              <td className={styles.muted}>{caseItem.asset_id ?? "Organization"}</td>
              <td className={styles.muted}>{formatTime(caseItem.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default async function CasesPage({
  searchParams,
}: Readonly<{
  searchParams: Promise<SearchParameters>;
}>) {
  const [session, parameters] = await Promise.all([getAuthenticatedSession(), searchParams]);
  const filters = filtersFrom(parameters);

  // The layout rejects an absent session before this component renders. This
  // guard keeps the data boundary fail-closed if the route is ever reused.
  if (session === null) {
    return <QueueError status={401} />;
  }

  let response: Awaited<ReturnType<typeof listTenantCases>>;
  try {
    response = await listTenantCases(session, {
      limit: 50,
      ...(filters.cursor === "" ? {} : { cursor: filters.cursor }),
      ...(filters.query === "" ? {} : { q: filters.query }),
      ...(filters.state === "" ? {} : { state: filters.state }),
      ...(filters.severity === "" ? {} : { severity: filters.severity }),
    });
  } catch (error) {
    return <QueueError status={isCaseQueueApiError(error) ? error.status : undefined} />;
  }

  const visibleCases = response.items;
  const hasActiveFilters = filters.query !== "" || filters.state !== "" || filters.severity !== "";

  return (
    <section className={`content-frame ${styles.queue}`} aria-labelledby="cases-title">
      <header className={styles.queueHeader}>
        <div>
          <p className="eyebrow">OPERATIONS / CASE QUEUE</p>
          <h1 id="cases-title">Cases</h1>
          <p className={styles.lede}>
            Tenant-scoped triage records from the signed NetOps API. Filters are carried in the URL so this exact operational view can be shared and recovered.
          </p>
        </div>
        <div>
          <QueueEvents />
          {canWriteCases(session) ? (
            <Link className="button button-primary" href="/cases/new">
              Create case
            </Link>
          ) : null}
        </div>
      </header>

      <CaseFilters filters={filters} />

      <div className={styles.queueMeta} aria-live="polite">
        <p className={styles.count}>
          {visibleCases.length} {visibleCases.length === 1 ? "case" : "cases"} in view
        </p>
        <p className={styles.windowNote}>Filtered and ordered by the API · updated timestamps are UTC</p>
      </div>

      {visibleCases.length === 0 ? <EmptyQueue filtered={hasActiveFilters} /> : <CaseTable cases={visibleCases} />}
      {response.next_cursor !== null && response.next_cursor !== undefined ? (
        <nav aria-label="Case queue pagination" className={styles.pagination}>
          <Link className="button button-primary" href={queueHref(filters, response.next_cursor)}>
            Load next cases
          </Link>
        </nav>
      ) : null}
    </section>
  );
}
