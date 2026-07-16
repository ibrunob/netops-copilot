import { getAuthenticatedCaseApi } from "@/lib/api/cases";
import { getAuthenticatedAccessToken } from "@/lib/auth/session";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const encoder = new TextEncoder();

/**
 * Browser-facing SSE relay. It authenticates only with the encrypted HttpOnly
 * web session, then relays the generated API stream without disclosing its
 * bearer credential to JavaScript in the browser.
 */
export async function GET(request: Request): Promise<Response> {
  if ((await getAuthenticatedAccessToken()) === null) {
    return new Response("Authentication is required.", { status: 401 });
  }

  const lastEventId = request.headers.get("last-event-id") ?? undefined;
  const events = getAuthenticatedCaseApi().streamCaseEvents({ lastEventId });
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for await (const event of events) {
          controller.enqueue(
            encoder.encode(
              `id: ${event.id}\nevent: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
            ),
          );
        }
      } catch {
        // No API error body is sent to a browser consumer. Closing lets the
        // native EventSource reconnection protocol recover from a transient
        // upstream failure while retaining Last-Event-ID.
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "content-type": "text/event-stream; charset=utf-8",
      "x-accel-buffering": "no",
    },
  });
}
