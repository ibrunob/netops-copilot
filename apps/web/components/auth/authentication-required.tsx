import Link from "next/link";

export function AuthenticationRequired() {
  return (
    <main className="auth-gate" aria-labelledby="authentication-required-title">
      <section className="auth-gate-card">
        <p className="eyebrow">IDENTITY BOUNDARY</p>
        <h1 id="authentication-required-title">Sign-in is required.</h1>
        <p>
          The product shell will not render without a verified enterprise session.
          Configure the OIDC adapter and API audience before enabling case access.
        </p>
        <Link className="button button-secondary" href="/">
          Return to product entry
        </Link>
      </section>
    </main>
  );
}
