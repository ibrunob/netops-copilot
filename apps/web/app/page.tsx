import Link from "next/link";

export default function HomePage() {
  return (
    <main className="landing-shell">
      <div className="landing-grid" aria-hidden="true" />
      <section className="landing-content" aria-labelledby="product-name">
        <p className="eyebrow">NETWORK OPERATIONS / EVIDENCE FIRST</p>
        <h1 id="product-name">NetOps Copilot</h1>
        <p className="landing-lede">
          A controlled workspace for triaging incidents, inspecting configuration
          evidence, and recording human-verified resolutions.
        </p>
        <div className="landing-actions">
          <Link className="button button-primary" href="/cases">
            Open case workspace
          </Link>
        </div>
        <p className="landing-footnote">
          Access is enforced by the enterprise OIDC boundary. The local product
          shell remains closed until that boundary is configured.
        </p>
      </section>
    </main>
  );
}
