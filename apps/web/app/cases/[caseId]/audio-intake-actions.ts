"use server";

import { z } from "zod";

import { NetOpsApiError } from "../../../../../packages/api-client/src/generated";

import { CaseQueueAccessError, getAuthenticatedCaseApi } from "@/lib/api/cases";

export type AudioUploadCapability = Readonly<{
  intentId: string;
  uploadUrl: string;
  requiredHeaders: Readonly<Record<string, string>>;
  expiresAt: string;
}>;

const audioMetadata = z.object({
  contentLength: z.number().int().positive().max(100 * 1024 * 1024),
  contentType: z.enum(["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/webm"]),
  originalFilename: z.string().min(1).max(255).nullable(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
});

/**
 * This action exchanges verified metadata for a short-lived capability only.
 * Audio bytes are deliberately never accepted by Next.js or the case API.
 */
export async function createAudioUploadIntentAction(
  caseId: string,
  metadata: unknown,
): Promise<AudioUploadCapability> {
  const parsed = audioMetadata.safeParse(metadata);
  if (!parsed.success) throw new Error("The audio upload metadata is invalid.");

  try {
    const capability = await getAuthenticatedCaseApi().createArtifactUploadIntent(caseId, {
      artifact_kind: "incident-audio",
      content_length: parsed.data.contentLength,
      content_type: parsed.data.contentType,
      original_filename: parsed.data.originalFilename,
      sha256: parsed.data.sha256,
    });
    return {
      intentId: capability.intent_id,
      uploadUrl: capability.upload_url,
      requiredHeaders: capability.required_headers,
      expiresAt: capability.expires_at,
    };
  } catch (error) {
    throw new Error(audioUploadErrorMessage(error));
  }
}

/** Completes a metadata-only, object-store HEAD verification. */
export async function completeAudioUploadAction(caseId: string, intentId: string): Promise<void> {
  try {
    await getAuthenticatedCaseApi().completeArtifactUploadIntent(caseId, intentId);
  } catch (error) {
    throw new Error(audioUploadErrorMessage(error));
  }
}

function audioUploadErrorMessage(error: unknown): string {
  if (error instanceof CaseQueueAccessError || (error instanceof NetOpsApiError && error.status === 401)) {
    return "Your session has expired. Sign in again before uploading audio evidence.";
  }
  if (error instanceof NetOpsApiError && error.status === 403) {
    return "Your verified role or case scope cannot upload audio evidence for this case.";
  }
  if (error instanceof NetOpsApiError && error.status === 404) {
    return "This case is no longer available in your organization scope.";
  }
  if (error instanceof NetOpsApiError && error.status === 409) {
    return "The upload capability expired or its object verification failed. Start a new audio upload.";
  }
  if (error instanceof NetOpsApiError && (error.status === 413 || error.status === 422)) {
    return "The audio metadata was rejected. Use an accepted format and a file no larger than 100 MiB.";
  }
  return "The audio upload service is unavailable. No evidence was completed.";
}
