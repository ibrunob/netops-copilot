"use client";

import { useEffect, useRef, useState, useTransition } from "react";

import { completeAudioUploadAction, createAudioUploadIntentAction } from "./audio-intake-actions";
import styles from "./workbench.module.css";

type UploadTone = "idle" | "working" | "success" | "error";
type UploadStatus = Readonly<{ tone: UploadTone; message: string }>;
type BrowserAudioType = "audio/mpeg" | "audio/wav" | "audio/x-wav" | "audio/mp4" | "audio/webm";

const MAX_AUDIO_BYTES = 100 * 1024 * 1024;
const idleStatus: UploadStatus = { tone: "idle", message: "" };

function supportedAudioType(blob: Blob): BrowserAudioType | null {
  const type = blob.type.toLowerCase().split(";", 1)[0];
  return type === "audio/mpeg" || type === "audio/wav" || type === "audio/x-wav" || type === "audio/mp4" || type === "audio/webm"
    ? type
    : null;
}

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function recorderMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return ["audio/webm;codecs=opus", "audio/webm"].find((type) => MediaRecorder.isTypeSupported(type));
}

export function AudioIntake({ caseId }: Readonly<{ caseId: string }>) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<Blob | null>(null);
  const filenameRef = useRef<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const resultRef = useRef<HTMLParagraphElement>(null);
  const [status, setStatus] = useState<UploadStatus>(idleStatus);
  const [recording, setRecording] = useState(false);
  const [isUploading, startUpload] = useTransition();
  const recordingAvailable = typeof window !== "undefined" && typeof MediaRecorder !== "undefined" && navigator.mediaDevices !== undefined;

  useEffect(() => () => {
    recorderRef.current?.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  useEffect(() => {
    if (status.tone === "error") resultRef.current?.focus();
  }, [status.tone]);

  function clearSelection() {
    audioRef.current = null;
    filenameRef.current = null;
    if (fileInputRef.current !== null) fileInputRef.current.value = "";
  }

  function selectAudio(file: File | null) {
    clearSelection();
    if (file === null) return;
    if (file.size > MAX_AUDIO_BYTES) {
      setStatus({ tone: "error", message: "Audio evidence must be 100 MiB or smaller." });
      return;
    }
    if (supportedAudioType(file) === null) {
      setStatus({ tone: "error", message: "Choose MP3, WAV, M4A/MP4, or WebM audio." });
      return;
    }
    audioRef.current = file;
    filenameRef.current = file.name;
    setStatus({ tone: "idle", message: "" });
  }

  async function startRecording() {
    if (!recordingAvailable || isUploading || recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = recorderMimeType();
      const recorder = mimeType === undefined ? new MediaRecorder(stream) : new MediaRecorder(stream, { mimeType });
      const chunks: BlobPart[] = [];
      streamRef.current = stream;
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        const audio = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        recorderRef.current = null;
        setRecording(false);
        if (audio.size === 0 || supportedAudioType(audio) === null || audio.size > MAX_AUDIO_BYTES) {
          clearSelection();
          setStatus({ tone: "error", message: "The recording could not be prepared as accepted audio evidence." });
          return;
        }
        clearSelection();
        audioRef.current = audio;
        filenameRef.current = "incident-audio-recording.webm";
        setStatus({ tone: "idle", message: "Recording ready for direct secure upload." });
      });
      clearSelection();
      recorder.start();
      setRecording(true);
      setStatus({ tone: "working", message: "Recording locally in this browser. No transcription is performed." });
    } catch {
      setStatus({ tone: "error", message: "Microphone access was unavailable. Choose an audio file instead." });
    }
  }

  function stopRecording() {
    recorderRef.current?.stop();
  }

  function uploadAudio() {
    const audio = audioRef.current;
    const contentType = audio === null ? null : supportedAudioType(audio);
    if (audio === null || contentType === null || isUploading || recording) {
      setStatus({ tone: "error", message: "Select or record accepted audio before uploading." });
      return;
    }
    startUpload(() => {
      void (async () => {
        setStatus({ tone: "working", message: "Requesting a one-time audio upload capability…" });
        try {
          const capability = await createAudioUploadIntentAction(caseId, {
            contentLength: audio.size,
            contentType,
            originalFilename: filenameRef.current,
            sha256: await sha256Hex(audio),
          });
          if (Date.parse(capability.expiresAt) <= Date.now()) {
            throw new Error("The one-time upload capability expired before the upload began. Start a new upload.");
          }
          setStatus({ tone: "working", message: "Uploading audio directly to protected artifact storage…" });
          const response = await fetch(capability.uploadUrl, { method: "PUT", headers: capability.requiredHeaders, body: audio });
          if (!response.ok) throw new Error(`Direct audio upload was rejected (${response.status}). No evidence was completed.`);
          setStatus({ tone: "working", message: "Verifying uploaded audio metadata…" });
          await completeAudioUploadAction(caseId, capability.intentId);
          clearSelection();
          setStatus({ tone: "success", message: "Audio upload verified. It is queued for the restricted processing pipeline." });
        } catch (error) {
          setStatus({ tone: "error", message: error instanceof Error ? error.message : "The audio upload could not be completed." });
        }
      })();
    });
  }

  return (
    <section aria-labelledby="audio-intake-title" className={`${styles.panel} ${styles.audioPanel}`}>
      <p className={styles.panelKicker}>Secure evidence / direct object upload</p>
      <h2 className={styles.panelTitle} id="audio-intake-title">Incident audio intake</h2>
      <p className={styles.configIntro}>Choose a recording or capture one locally. Audio goes directly from this browser to restricted artifact storage; this app does not transcribe or inspect it.</p>
      <div className={styles.audioControls}>
        <label className={styles.fileInput}>
          Choose audio evidence
          <input accept="audio/mpeg,audio/wav,audio/x-wav,audio/mp4,audio/webm,.mp3,.wav,.m4a,.mp4,.webm" disabled={isUploading || recording} onChange={(event) => selectAudio(event.target.files?.item(0) ?? null)} ref={fileInputRef} type="file" />
        </label>
        {recordingAvailable ? (
          <button disabled={isUploading} onClick={recording ? stopRecording : startRecording} type="button">
            {recording ? "Stop recording" : "Record in browser"}
          </button>
        ) : <p className={styles.formHelp}>Browser recording is unavailable here; choose an audio file.</p>}
        <button disabled={isUploading || recording} onClick={uploadAudio} type="button">
          {isUploading ? "Secure upload in progress…" : "Upload audio evidence"}
        </button>
      </div>
      <p className={styles.formHelp}>Accepted: MP3, WAV, M4A/MP4, and WebM. Maximum size: 100 MiB. No client-side speech-to-text is used.</p>
      {status.tone !== "idle" ? <p aria-atomic="true" aria-live="polite" className={styles.actionResult} data-status={status.tone} ref={resultRef} role={status.tone === "error" ? "alert" : "status"} tabIndex={-1}>{status.message}</p> : null}
    </section>
  );
}
