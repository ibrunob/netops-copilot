"use server";

import { NetOpsApiError, type CaseState } from "../../../../../packages/api-client/src/generated";

import { getAuthenticatedCaseApi } from "@/lib/api/cases";

export type CaseActionResult = Readonly<{
  status: "idle" | "success" | "conflict" | "error";
  message: string;
}>;

export const idleCaseActionResult: CaseActionResult = {
  status: "idle",
  message: "",
};

const caseStates = new Set<CaseState>([
  "new",
  "investigating",
  "diagnosed",
  "fix_proposed",
  "needs_information",
  "confirmed",
  "resolved",
  "learned",
]);

function requiredText(formData: FormData, name: string, maximum = 10_000): string | null {
  const value = formData.get(name);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= maximum ? trimmed : null;
}

function optionalText(formData: FormData, name: string, maximum = 10_000): string | null {
  const value = formData.get(name);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  return trimmed.length <= maximum ? trimmed : null;
}

function expectedVersion(formData: FormData): number | null {
  const value = requiredText(formData, "expected_version", 16);
  if (value === null || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function isCaseState(value: string): value is CaseState {
  return caseStates.has(value as CaseState);
}

function actionFailure(error: unknown): CaseActionResult {
  if (error instanceof NetOpsApiError && error.status === 409) {
    return {
      status: "conflict",
      message: "This case changed while you were working. Refresh the case, review the new timeline, then reapply the action.",
    };
  }

  if (error instanceof NetOpsApiError && error.status === 403) {
    return {
      status: "error",
      message: "Your verified role cannot perform this action.",
    };
  }

  if (error instanceof NetOpsApiError && error.status === 404) {
    return {
      status: "error",
      message: "This case is no longer available in your organization scope.",
    };
  }

  return {
    status: "error",
    message: "The action was not recorded. Check your connection and try again.",
  };
}

export async function transitionCaseAction(
  caseId: string,
  _previous: CaseActionResult,
  formData: FormData,
): Promise<CaseActionResult> {
  const version = expectedVersion(formData);
  const target = requiredText(formData, "to_state", 40);
  if (version === null || target === null || !isCaseState(target)) {
    return { status: "error", message: "Choose a valid state before continuing." };
  }

  const note = optionalText(formData, "note");
  const approvalId = optionalText(formData, "approval_id", 100);
  if (target === "needs_information" && note === null) {
    return { status: "error", message: "A clear request for information is required." };
  }
  if (target === "confirmed" && approvalId === null) {
    return { status: "error", message: "Confirmation requires the immutable approval record ID." };
  }

  try {
    await (await getAuthenticatedCaseApi()).transitionCase(caseId, {
      expected_version: version,
      to_state: target,
      ...(note === null ? {} : { note }),
      ...(approvalId === null ? {} : { approval_id: approvalId }),
    });
    return { status: "success", message: "State transition recorded. Refreshing the case timeline…" };
  } catch (error) {
    return actionFailure(error);
  }
}

export async function resolveCaseAction(
  caseId: string,
  _previous: CaseActionResult,
  formData: FormData,
): Promise<CaseActionResult> {
  const version = expectedVersion(formData);
  const verificationNote = requiredText(formData, "verification_note");
  if (version === null || verificationNote === null) {
    return { status: "error", message: "A verification note is required to resolve this case." };
  }

  try {
    await (await getAuthenticatedCaseApi()).resolveCase(caseId, {
      expected_version: version,
      verification_note: verificationNote,
    });
    return { status: "success", message: "Resolution recorded. Refreshing the case timeline…" };
  } catch (error) {
    return actionFailure(error);
  }
}

export async function requestFeedbackAction(
  caseId: string,
  _previous: CaseActionResult,
  formData: FormData,
): Promise<CaseActionResult> {
  const version = expectedVersion(formData);
  const note = requiredText(formData, "note");
  if (version === null || note === null) {
    return { status: "error", message: "Write the information needed before sending the request." };
  }

  try {
    await (await getAuthenticatedCaseApi()).requestCaseFeedback(caseId, {
      expected_version: version,
      note,
    });
    return { status: "success", message: "Feedback request recorded. Refreshing the case timeline…" };
  } catch (error) {
    return actionFailure(error);
  }
}
