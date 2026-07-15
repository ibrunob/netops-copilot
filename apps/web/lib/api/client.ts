import "server-only";

import { getServerEnvironment } from "@/lib/config/server";

export type AccessTokenProvider = Readonly<{
  getAccessToken: () => Promise<string | null>;
}>;

export type ApiClientOptions = Readonly<{
  accessTokenProvider: AccessTokenProvider;
  baseUrl?: string;
  fetchImplementation?: typeof fetch;
}>;

export class ApiResponseError extends Error {
  public readonly status: number;
  public readonly correlationId: string | null;

  public constructor(
    status: number,
    correlationId: string | null,
    message = "The NetOps API request failed.",
  ) {
    super(message);
    this.name = "ApiResponseError";
    this.status = status;
    this.correlationId = correlationId;
  }
}

/**
 * Server-side boundary for the generated `/v1` client. UI code must supply an
 * access-token provider derived from the verified OIDC session; it may not read
 * browser storage or accept a tenant identifier from the caller.
 */
export class NetOpsApiClient {
  private readonly accessTokenProvider: AccessTokenProvider;
  private readonly baseUrl: string;
  private readonly fetchImplementation: typeof fetch;

  public constructor({
    accessTokenProvider,
    baseUrl = getServerEnvironment().NETOPS_API_BASE_URL,
    fetchImplementation = fetch,
  }: ApiClientOptions) {
    this.accessTokenProvider = accessTokenProvider;
    this.baseUrl = baseUrl;
    this.fetchImplementation = fetchImplementation;
  }

  public async request<TResponse>(
    path: `/v1/${string}`,
    init: Omit<RequestInit, "headers"> & { headers?: HeadersInit } = {},
  ): Promise<TResponse> {
    const accessToken = await this.accessTokenProvider.getAccessToken();

    if (accessToken === null) {
      throw new ApiResponseError(401, null, "A verified access token is required.");
    }

    const response = await this.fetchImplementation(new URL(path, this.baseUrl), {
      ...init,
      cache: "no-store",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${accessToken}`,
        ...init.headers,
      },
    });

    if (!response.ok) {
      throw new ApiResponseError(
        response.status,
        response.headers.get("x-correlation-id"),
      );
    }

    return (await response.json()) as TResponse;
  }
}
