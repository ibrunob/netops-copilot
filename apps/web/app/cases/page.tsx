import Link from "next/link";

export const metadata = {
  title: "Cases",
};

export default function CasesPage() {
  return (
    <section className="content-frame" aria-labelledby="cases-title">
      <div className="page-heading">
        <div>
          <p className="eyebrow">OPERATIONS / CASE QUEUE</p>
          <h1 id="cases-title">Cases</h1>
          <p className="muted-copy">
            The case queue will load organization-scoped records from the versioned
            domain API. No browser-local incident data is used.
          </p>
        </div>
        <Link className="button button-primary" href="/cases/new">
          Create case
        </Link>
      </div>

      <section className="empty-state" aria-labelledby="queue-contract-title">
        <p className="empty-state-index" aria-hidden="true">
          01
        </p>
        <div>
          <h2 id="queue-contract-title">Queue contract pending</h2>
          <p>
            This route is intentionally free of demo records. It will use the
            generated OpenAPI client for cursor pagination, filters, full-text
            search, and persisted SSE updates once the case API contract lands.
          </p>
        </div>
      </section>
    </section>
  );
}
