import Link from "next/link";

export function AuthenticationRequired() {
  return (
    <main className="auth-gate" aria-labelledby="authentication-required-title">
      <section className="auth-gate-card">
        <p className="eyebrow">IDENTITY BOUNDARY</p>
        <h1 id="authentication-required-title">Sign-in is required.</h1>
        <p>
          The product shell will not render without a verified enterprise session.
          Sign in through your configured identity provider to continue.
        </p>
        <div className="auth-gate-actions">
          <a className="button button-primary" href="/auth/login">
            Sign in securely
          </a>
          <Link className="button button-secondary" href="/">
            Return to product entry
          </Link>
        </div>
      </section>
    </main>
  );
}
