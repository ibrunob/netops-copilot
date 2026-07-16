# NetOps Copilot demo runbook

This is a real local demonstration: every case, transition, audit event, and
artifact status is backed by the Compose stack. The sample incidents are seeded
into PostgreSQL, not held in browser storage.

## Before recording or presenting

```sh
make demo
```

Open `http://localhost:3000` and sign in with the local-only account:

```text
Login: demo-operator / netops-demo
```

If the queue is empty, sign in once, run `make seed`, then refresh. This first
sign-in creates the local operator record that the seed contract references.

## Two-minute product story

1. **Start in the case queue.** Point out that states are operational rather
   than cosmetic: one case is new, one is being investigated, one is explicitly
   waiting for a customer answer, one is approved, and one is resolved.
2. **Open “Customer request: firewall inbound NAT.”** Show the activity timeline
   and its `Pending customer answer` state. This demonstrates that the product
   manages support requests as cases, not just machine alerts.
3. **Open “Madrid core: intermittent OSPF adjacency resets.”** Use the state
   rail to begin investigation. The immutable timeline updates through the
   authenticated API and is visible from another browser session via SSE.
4. **Add evidence.** Paste a small configuration excerpt and show the redacted
   preview before confirmation. Optionally upload a harmless small audio file
   and wait for its scan/status indicator. Do not use secrets in a recording.
5. **Create a live case.** Return to **Open a case**, enter a title and a
   severity, then submit. The form uses an idempotency key and a same-origin
   fallback, so a browser navigation issue cannot silently duplicate the case.
6. **Close on the safety boundary.** NetOps Copilot can organize evidence and
   human approvals, but it never writes to a router or firewall. This is the
   product decision that makes an AI-assisted operations workflow safe to adopt.

## Presenter recovery

- Use `make demo` again to confirm the stack and reseed without duplicate demo
  records.
- If the OIDC session expires, return to the landing page and sign in again.
- Do not present `0.0.0.0` in a normal desktop browser; use `localhost` unless
  you configured the in-app-browser settings described in the root README.
- Do not show `.env`, MinIO credentials, Keycloak administration, or raw
  customer configuration.
