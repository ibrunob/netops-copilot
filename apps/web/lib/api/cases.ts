import "server-only";

import {
  type CaseDetailResponse,
  type CaseEventStreamEvent,
  type CaseEventStreamOptions,
  type CaseFeedbackRequest,
  type CaseListOptions,
  NetOpsApiError,
  NetOpsCaseClient,
  type CaseListResponse,
  type CaseResponse,
  type CaseTimelineEntryResponse,
  type ConfigPreviewRequest,
  type ConfigPreviewResponse,
  type ArtifactUploadCapabilityResponse,
  type ArtifactUploadCompletionResponse,
  type ArtifactUploadIntentRequest,
  type CaseArtifactStatusListResponse,
  type CreateCaseRequest,
  type ResolveCaseRequest,
  type TransitionCaseRequest,
} from "../../../../packages/api-client/src/generated";

import { getServerEnvironment } from "@/lib/config/server";
import { getAuthenticatedAccessToken } from "@/lib/auth/session";

export type CaseApiSession = Readonly<{
  /** Display-safe verified session marker; its token is obtained separately. */
  subject: string;
}>;

export class CaseQueueAccessError extends Error {
  public readonly status = 401;

  public constructor() {
    super("The verified session does not contain an API access token.");
    this.name = "CaseQueueAccessError";
  }
}

/** Server-only API surface for a single verified session. */
export type AuthenticatedCaseApi = Readonly<{
  listCases: (options?: number | CaseListOptions) => Promise<CaseListResponse>;
  createCase: (body: CreateCaseRequest, idempotencyKey: string) => Promise<CaseResponse>;
  getCase: (caseId: string) => Promise<CaseDetailResponse>;
  getCaseTimeline: (caseId: string) => Promise<CaseTimelineEntryResponse[]>;
  transitionCase: (caseId: string, body: TransitionCaseRequest) => Promise<CaseResponse>;
  resolveCase: (caseId: string, body: ResolveCaseRequest) => Promise<CaseResponse>;
  requestCaseFeedback: (caseId: string, body: CaseFeedbackRequest) => Promise<CaseResponse>;
  previewCaseConfig: (caseId: string, body: ConfigPreviewRequest) => Promise<ConfigPreviewResponse>;
  createArtifactUploadIntent: (
    caseId: string,
    body: ArtifactUploadIntentRequest,
  ) => Promise<ArtifactUploadCapabilityResponse>;
  completeArtifactUploadIntent: (
    caseId: string,
    intentId: string,
  ) => Promise<ArtifactUploadCompletionResponse>;
  listCaseArtifactStatuses: (caseId: string) => Promise<CaseArtifactStatusListResponse>;
  streamCaseEvents: (
    options?: CaseEventStreamOptions,
  ) => AsyncGenerator<CaseEventStreamEvent>;
}>;

async function authenticatedClient(): Promise<NetOpsCaseClient> {
  const accessToken = await getAuthenticatedAccessToken();
  if (accessToken === null || accessToken.length === 0) {
    throw new CaseQueueAccessError();
  }

  return new NetOpsCaseClient({
    baseUrl: getServerEnvironment().NETOPS_API_BASE_URL,
    accessToken: async () => accessToken,
  });
}

/**
 * Provides case read/write operations without exposing the credential to a
 * client component. Each operation resolves the encrypted server session at
 * call time, so a deleted or expired session fails closed.
 */
export function getAuthenticatedCaseApi(): AuthenticatedCaseApi {
  return {
    listCases: async (options) => (await authenticatedClient()).listCases(options),
    createCase: async (body, idempotencyKey) =>
      (await authenticatedClient()).createCase(body, idempotencyKey),
    getCase: async (caseId) => (await authenticatedClient()).getCase(caseId),
    getCaseTimeline: async (caseId) => (await authenticatedClient()).getCaseTimeline(caseId),
    transitionCase: async (caseId, body) =>
      (await authenticatedClient()).transitionCase(caseId, body),
    resolveCase: async (caseId, body) => (await authenticatedClient()).resolveCase(caseId, body),
    requestCaseFeedback: async (caseId, body) =>
      (await authenticatedClient()).requestCaseFeedback(caseId, body),
    previewCaseConfig: async (caseId, body) =>
      (await authenticatedClient()).previewCaseConfig(caseId, body),
    createArtifactUploadIntent: async (caseId, body) =>
      (await authenticatedClient()).createArtifactUploadIntent(caseId, body),
    completeArtifactUploadIntent: async (caseId, intentId) =>
      (await authenticatedClient()).completeArtifactUploadIntent(caseId, intentId),
    listCaseArtifactStatuses: async (caseId) =>
      (await authenticatedClient()).listCaseArtifactStatuses(caseId),
    streamCaseEvents: async function* (options) {
      yield* (await authenticatedClient()).streamCaseEvents(options);
    },
  };
}

/**
 * The only queue-to-API seam. It intentionally uses the generated client,
 * passes no tenant data from the UI, and keeps the OIDC token on the server.
 */
export async function listTenantCases(
  _session: CaseApiSession,
  options: CaseListOptions = {},
): Promise<CaseListResponse> {
  const client = await authenticatedClient();
  return client.listCases(options);
}

/**
 * Retains the generated event stream contract at the server boundary. The BFF
 * route owns the client-facing stream so the browser never receives a token.
 */
export async function* streamTenantCaseEvents(
  _session: CaseApiSession,
  lastEventId?: string,
): AsyncGenerator<CaseEventStreamEvent> {
  const client = await authenticatedClient();
  yield* client.streamCaseEvents({ lastEventId });
}

export function isCaseQueueApiError(error: unknown): error is NetOpsApiError {
  return error instanceof NetOpsApiError;
}
