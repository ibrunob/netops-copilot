"use client";

import { useActionState, useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import {
  completeConfigUploadAction,
  createConfigUploadIntentAction,
  idleConfigPreviewActionResult,
  previewCaseConfigAction,
} from "./config-preview-actions";
import styles from "./workbench.module.css";

type UploadStatus = Readonly<{ tone: "idle" | "error" | "success" | "working"; message: string }>;
type SelectedFileMetadata = Readonly<{ name: string; type: string }>;

const idleUploadStatus: UploadStatus = { tone: "idle", message: "" };

function contentTypeFor(file: SelectedFileMetadata | null): "text/plain" | "application/json" {
  return file?.type === "application/json" || file?.name.toLowerCase().endsWith(".json")
    ? "application/json"
    : "text/plain";
}

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function ConfigPreview({ caseId }: Readonly<{ caseId: string }>) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fileMetadataRef = useRef<SelectedFileMetadata | null>(null);
  const resultRef = useRef<HTMLParagraphElement>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<UploadStatus>(idleUploadStatus);
  const [isUploading, startUpload] = useTransition();
  const [result, action, previewPending] = useActionState(
    previewCaseConfigAction.bind(null, caseId),
    idleConfigPreviewActionResult,
  );

  useEffect(() => {
    if (result.status === "error") resultRef.current?.focus();
  }, [result.status]);

  useEffect(() => {
    if (uploadStatus.tone === "error") resultRef.current?.focus();
  }, [uploadStatus.tone]);

  function useSelectedFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.item(0) ?? null;
    fileMetadataRef.current = file === null ? null : { name: file.name, type: file.type };
    if (file === null) return;
    if (file.size > 256 * 1024) {
      setUploadStatus({ tone: "error", message: "Selected configuration exceeds the 256 KiB redaction-preview limit." });
      event.target.value = "";
      fileMetadataRef.current = null;
      return;
    }
    void file.text().then((contents) => {
      // This is deliberately an uncontrolled DOM field, never React state.
      if (textRef.current !== null) textRef.current.value = contents;
      setUploadStatus({ tone: "idle", message: "" });
    }).catch(() => {
      fileMetadataRef.current = null;
      setUploadStatus({ tone: "error", message: "The selected file could not be read for redaction preview." });
    });
  }

  function uploadRedactionReviewedConfig() {
    const rawConfig = textRef.current?.value ?? "";
    if (!confirmed || result.preview === undefined || rawConfig.length === 0) return;

    startUpload(() => {
      void (async () => {
        setUploadStatus({ tone: "working", message: "Requesting a one-time upload capability…" });
        try {
          const selectedFile = fileMetadataRef.current;
          const contentType = contentTypeFor(selectedFile);
          const blob = new Blob([rawConfig], { type: contentType });
          const capability = await createConfigUploadIntentAction(caseId, {
            contentLength: blob.size,
            contentType,
            originalFilename: selectedFile?.name ?? null,
            sha256: await sha256Hex(blob),
          });
          const expiresAt = Date.parse(capability.expiresAt);
          if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
            throw new Error("The one-time upload capability expired before the upload began. Preview again to request a new one.");
          }
          setUploadStatus({ tone: "working", message: "Uploading directly to protected artifact storage…" });
          const uploadResponse = await fetch(capability.uploadUrl, {
            method: "PUT",
            headers: capability.requiredHeaders,
            body: blob,
          });
          if (!uploadResponse.ok) {
            throw new Error(`Direct upload was rejected (${uploadResponse.status}). No artifact was completed.`);
          }
          setUploadStatus({ tone: "working", message: "Verifying uploaded metadata…" });
          await completeConfigUploadAction(caseId, capability.intentId);
          if (textRef.current !== null) textRef.current.value = "";
          fileMetadataRef.current = null;
          formRef.current?.reset();
          setConfirmed(false);
          setUploadStatus({ tone: "success", message: "Configuration upload verified. It is now queued for the restricted processing pipeline." });
          router.refresh();
        } catch (error) {
          setUploadStatus({
            tone: "error",
            message: error instanceof Error ? error.message : "The upload could not be completed. The configuration remains only in this form.",
          });
        }
      })();
    });
  }

  function requestPreview(formData: FormData) {
    setConfirmed(false);
    action(formData);
  }

  return (
    <section aria-labelledby="config-preview-title" className={`${styles.panel} ${styles.configPanel}`}>
      <p className={styles.panelKicker}>Secure intake / direct object upload</p>
      <h2 className={styles.panelTitle} id="config-preview-title">Configuration redaction gate</h2>
      <p className={styles.configIntro}>
        Preview a pasted or selected configuration before upload. The browser holds the original only in this form until
        direct upload is verified; the application receives only the redacted preview and upload metadata.
      </p>
      <form action={requestPreview} className={styles.configForm} ref={formRef}>
        <label>
          Configuration paste
          <textarea
            aria-describedby="config-preview-help"
            maxLength={256 * 1024}
            name="config"
            placeholder="Paste configuration for a redacted preview"
            ref={textRef}
            required
            spellCheck="false"
          />
        </label>
        <label className={styles.fileInput}>
          Or choose a text/JSON configuration
          <input accept="text/plain,application/json,.txt,.cfg,.conf,.json" onChange={useSelectedFile} type="file" />
        </label>
        <p className={styles.formHelp} id="config-preview-help">
          Preview limit: 256 KiB. Secret values are redacted server-side before they are returned here.
        </p>
        <button disabled={previewPending || isUploading} type="submit">{previewPending ? "Preparing preview…" : "Preview redaction"}</button>
      </form>
      {result.status !== "idle" || uploadStatus.tone !== "idle" ? (
        <p
          aria-atomic="true"
          aria-live="polite"
          className={styles.actionResult}
          data-status={uploadStatus.tone === "idle" ? result.status : uploadStatus.tone}
          ref={resultRef}
          role={result.status === "error" || uploadStatus.tone === "error" ? "alert" : "status"}
          tabIndex={-1}
        >
          {uploadStatus.tone === "idle" ? result.message : uploadStatus.message}
        </p>
      ) : null}
      {result.preview !== undefined ? (
        <div className={styles.previewResult}>
          <dl className={styles.previewFacts}>
            <div><dt>Lines</dt><dd>{result.preview.sourceLineCount}</dd></div>
            <div><dt>Lines redacted</dt><dd>{result.preview.redactedLineCount}</dd></div>
            <div><dt>Redaction policy</dt><dd>{result.preview.redactionVersion}</dd></div>
          </dl>
          <pre aria-label="Redacted configuration preview">{result.preview.redactedContent}</pre>
          <p className={styles.previewHash}>Redacted derivative SHA-256: {result.preview.redactedContentSha256}</p>
          <label className={styles.confirmation}>
            <input checked={confirmed} disabled={isUploading} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
            I reviewed the redacted preview. Upload the original configuration directly to restricted artifact storage.
          </label>
          <button disabled={!confirmed || isUploading} onClick={uploadRedactionReviewedConfig} type="button">
            {isUploading ? "Secure upload in progress…" : "Upload reviewed configuration"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
