"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";

import { NetOpsApiError, type CreateCaseRequest } from "../../../../../packages/api-client/src/generated";

import { CaseQueueAccessError, getAuthenticatedCaseApi } from "@/lib/api/cases";

export type CreateCaseActionResult = Readonly<{
  status: "idle" | "success" | "error";
  message: string;
  caseId?: string;
}>;

const createCaseFields = z.object({
  idempotencyKey: z.string().trim().min(1).max(255),
  title: z.string().trim().min(1, "Enter a case title.").max(500),
  severity: z.enum(["low", "medium", "high", "critical"]),
  category: z.string().trim().max(100),
  assetId: z.string().trim(),
  inputKind: z.string().trim().max(100),
  inputContent: z.string().trim(),
});

function field(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value : "";
}

function requestFrom(formData: FormData):
  | { request: CreateCaseRequest; idempotencyKey: string }
  | { message: string } {
  const parsed = createCaseFields.safeParse({
    idempotencyKey: field(formData, "idempotency_key"),
    title: field(formData, "title"),
    severity: field(formData, "severity"),
    category: field(formData, "category"),
    assetId: field(formData, "asset_id"),
    inputKind: field(formData, "input_kind"),
    inputContent: field(formData, "input_content"),
  });

  if (!parsed.success) {
    return { message: parsed.error.issues[0]?.message ?? "Check the case details and try again." };
  }

  const { assetId, category, idempotencyKey, inputContent, inputKind, severity, title } = parsed.data;
  if (assetId !== "" && !z.uuid().safeParse(assetId).success) {
    return { message: "Asset ID must be a valid UUID, or left blank for an organization-wide case." };
  }

  if ((inputKind === "") !== (inputContent === "")) {
    return { message: "Provide both an input kind and a JSON object, or leave both evidence fields blank." };
  }

  let input: CreateCaseRequest["input"];
  if (inputContent !== "") {
    let content: unknown;
    try {
      content = JSON.parse(inputContent);
    } catch {
      return { message: "Evidence content must be valid JSON." };
    }
    if (content === null || Array.isArray(content) || typeof content !== "object") {
      return { message: "Evidence content must be a JSON object, not a list or scalar value." };
    }
    input = { input_kind: inputKind, content: content as Record<string, unknown> };
  }

  return {
    idempotencyKey,
    request: {
      title,
      severity,
      ...(category === "" ? {} : { category }),
      ...(assetId === "" ? {} : { asset_id: assetId }),
      ...(input === undefined ? {} : { input }),
    },
  };
}

function failure(error: unknown): CreateCaseActionResult {
  if (error instanceof CaseQueueAccessError || (error instanceof NetOpsApiError && error.status === 401)) {
    return { status: "error", message: "Your session has expired. Sign in again before creating a case." };
  }
  if (error instanceof NetOpsApiError && error.status === 403) {
    return { status: "error", message: "Your verified role or asset scope cannot create this case." };
  }
  if (error instanceof NetOpsApiError && error.status === 409) {
    return {
      status: "error",
      message: "This idempotency key is already associated with a different request. Edit the form, then submit again.",
    };
  }
  if (error instanceof NetOpsApiError && error.status === 422) {
    return { status: "error", message: "The API rejected these case details. Review the fields and try again." };
  }
  return {
    status: "error",
    message: "The case may not have reached the API. Keep this form unchanged and retry to safely recover the submission.",
  };
}

export async function createCaseAction(
  _previous: CreateCaseActionResult,
  formData: FormData,
): Promise<CreateCaseActionResult> {
  const candidate = requestFrom(formData);
  if ("message" in candidate) return { status: "error", message: candidate.message };

  try {
    const created = await getAuthenticatedCaseApi().createCase(candidate.request, candidate.idempotencyKey);
    revalidatePath("/cases");
    return {
      status: "success",
      message: "The API accepted this case. Opening the immutable case record…",
      caseId: created.id,
    };
  } catch (error) {
    return failure(error);
  }
}
