import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(path.join(webRoot, relativePath), "utf8");
}

test("case routes remain protected by the server session boundary", async () => {
  const layout = await source("app/cases/layout.tsx");
  const session = await source("lib/auth/session.ts");

  assert.match(layout, /await getAuthenticatedSession\(\)/);
  assert.match(layout, /if \(session === null\)/);
  assert.match(layout, /<AuthenticationRequired \/>/);
  assert.match(session, /return null;/);
});

test("the server API boundary requires a verified token provider", async () => {
  const client = await source("lib/api/client.ts");

  assert.match(client, /getAccessToken: \(\) => Promise<string \| null>/);
  assert.match(client, /if \(accessToken === null\)/);
  assert.match(client, /authorization: `Bearer \$\{accessToken\}`/);
  assert.doesNotMatch(client, /localStorage|sessionStorage/);
});
