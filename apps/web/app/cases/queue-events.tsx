"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import styles from "./cases.module.css";

type StreamStatus = "connecting" | "connected" | "reconnecting";

const statusLabel: Record<StreamStatus, string> = {
  connecting: "Connecting to case updates",
  connected: "Live updates connected",
  reconnecting: "Reconnecting to case updates",
};

/**
 * Browser-side event receiver for the authenticated BFF proxy. EventSource
 * automatically carries Last-Event-ID across reconnects; the proxy owns bearer
 * use and replays the generated API stream without exposing it to this client.
 */
export function QueueEvents() {
  const router = useRouter();
  const [status, setStatus] = useState<StreamStatus>("connecting");

  useEffect(() => {
    const stream = new EventSource("/api/events");
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRefresh = () => {
      if (refreshTimer !== undefined) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined;
        router.refresh();
      }, 180);
    };

    stream.addEventListener("open", () => setStatus("connected"));
    stream.addEventListener("case", scheduleRefresh);
    stream.addEventListener("error", () => setStatus("reconnecting"));

    return () => {
      if (refreshTimer !== undefined) clearTimeout(refreshTimer);
      stream.close();
    };
  }, [router]);

  return (
    <p className={styles.liveStamp} data-stream-status={status} role="status">
      {statusLabel[status]}
    </p>
  );
}
