"use server";

import { z } from "zod";

import { NetOpsApiError } from "../../../../../packages/api-client/src/generated";

import { CaseQueueAccessError, getAuthenticatedCaseApi } from "@/lib/api/cases";

export type ConfigPreviewActionResult = Readonly<{
  status: "idle" | "success" | "error";
  message: string;
  preview?: Readonly<{
    redactedContent: string;
    redactedContentSha256: string;
    redactionVersion: string;
    sourceLineCount: number;
    redactedLineCount: number;
  }>;
}>;

export const idleConfigPreviewActionResult: ConfigPreviewActionResult = {
  status: "idle",
  message: "",
};

export type ArtifactUploadCapability = Readonly<{
  artifactId: string;
  intentId: string;
  uploadUrl: string;
  requiredHeaders: Readonly<Record<string, string>>;
  expiresAt: string;
}>;

const uploadMetadata = z.object({
  contentLength: z.number().int().positive().max(100 * 1024 * 1024),
  contentType: z.enum(["text/plain", "application/json"]),
  originalFilename: z.string().min(1).max(255).nullable(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
});

const configFields = z.object({
  config: z.string().min(1, "Paste a configuration before requesting a preview.").max(256 * 1024),
});

function configFrom(formData: FormData): string {
  const value = formData.get("config");
  return typeof value === "string" ? value : "";
}

export async function previewCaseConfigAction(
  caseId: string,
  _previous: ConfigPreviewActionResult,
  formData: FormData,
): Promise<ConfigPreviewActionResult> {
  const parsed = configFields.safeParse({ config: configFrom(formData) });
  if (!parsed.success) {
    return { status: "error", message: parsed.error.issues[0]?.message ?? "Check the configuration and try again." };
  }

  try {
    const preview = await getAuthenticatedCaseApi().previewCaseConfig(caseId, { config: parsed.data.config });
    return {
      status: "success",
      message: "Redacted preview prepared. The original paste was not retained by this screen.",
      preview: {
        redactedContent: preview.redacted_content,
        redactedContentSha256: preview.redacted_content_sha256,
        redactionVersion: preview.redaction_version,
        sourceLineCount: preview.report.source_line_count,
        redactedLineCount: preview.report.redacted_line_count,
      },
    };
  } catch (error) {
    if (error instanceof CaseQueueAccessError || (error instanceof NetOpsApiError && error.status === 401)) {
      return { status: "error", message: "Your session has expired. Sign in again before previewing a configuration." };
    }
    if (error instanceof NetOpsApiError && error.status === 403) {
      return { status: "error", message: "Your verified role or case scope cannot preview configuration for this case." };
    }
    if (error instanceof NetOpsApiError && error.status === 404) {
      return { status: "error", message: "This case is no longer available in your organization scope." };
    }
    if (error instanceof NetOpsApiError && error.status === 422) {
      return { status: "error", message: "The API rejected this configuration. It may exceed the preview safety limits." };
    }
    return { status: "error", message: "The preview did not reach the API. Nothing was stored by this screen." };
  }
}

/**
 * Requests only a one-time capability. Artifact bytes never cross this server
 * action; the browser uploads them directly to the object store.
 */
export async function createConfigUploadIntentAction(
  caseId: string,
  metadata: unknown,
): Promise<ArtifactUploadCapability> {
  const parsed = uploadMetadata.safeParse(metadata);
  if (!parsed.success) throw new Error("The configuration upload metadata is invalid.");

  try {
    const capability = await getAuthenticatedCaseApi().createArtifactUploadIntent(caseId, {
      artifact_kind: "network-configuration",
      content_length: parsed.data.contentLength,
      content_type: parsed.data.contentType,
      original_filename: parsed.data.originalFilename,
      sha256: parsed.data.sha256,
    });
    return {
      artifactId: capability.artifact_id,
      intentId: capability.intent_id,
      uploadUrl: capability.upload_url,
      requiredHeaders: capability.required_headers,
      expiresAt: capability.expires_at,
    };
  } catch (error) {
    throw new Error(uploadErrorMessage(error));
  }
}

/** Completes a HEAD-verified direct upload without accepting artifact bytes. */
export async function completeConfigUploadAction(caseId: string, intentId: string): Promise<void> {
  try {
    await getAuthenticatedCaseApi().completeArtifactUploadIntent(caseId, intentId);
  } catch (error) {
    throw new Error(uploadErrorMessage(error));
  }
}

function uploadErrorMessage(error: unknown): string {
  if (error instanceof CaseQueueAccessError || (error instanceof NetOpsApiError && error.status === 401)) {
    return "Your session has expired. Sign in again before uploading a configuration.";
  }
  if (error instanceof NetOpsApiError && error.status === 403) {
    return "Your verified role or case scope cannot upload a configuration for this case.";
  }
  if (error instanceof NetOpsApiError && error.status === 404) {
    return "This case is no longer available in your organization scope.";
  }
  if (error instanceof NetOpsApiError && error.status === 409) {
    return "The upload capability expired or its object verification failed. Request a new preview and try again.";
  }
  if (error instanceof NetOpsApiError && (error.status === 413 || error.status === 422)) {
    return "The API rejected this upload metadata. Check the file type and size.";
  }
  return "The upload service is unavailable. No artifact was completed.";
}
