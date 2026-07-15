export const metadata = {
  title: "New case",
};

export default function NewCasePage() {
  return (
    <section className="content-frame" aria-labelledby="new-case-title">
      <p className="eyebrow">OPERATIONS / INTAKE</p>
      <h1 id="new-case-title">Create a case</h1>
      <p className="muted-copy intake-intro">
        Case intake becomes available after the server validates the authenticated
        organization, idempotency key, input classification, and versioned case
        schema. This shell intentionally does not emulate a submission.
      </p>

      <dl className="intake-contract">
        <div>
          <dt>Required API</dt>
          <dd>
            <code>POST /v1/cases</code> with an idempotency key.
          </dd>
        </div>
        <div>
          <dt>Authorization</dt>
          <dd>Resolved from the OIDC principal; never selected in the browser.</dd>
        </div>
        <div>
          <dt>Next integration</dt>
          <dd>Generated types plus Zod validation at the UI boundary.</dd>
        </div>
      </dl>
    </section>
  );
}
