import Link from "next/link";
import { notFound } from "next/navigation";

import type {
  ArtifactStatusResponse,
  CaseDetailResponse,
} from "../../../../../packages/api-client/src/generated";

import { getAuthenticatedCaseApi } from "@/lib/api/cases";
import { canWriteCases, getAuthenticatedSession } from "@/lib/auth/session";

import { CaseWorkbench } from "./case-workbench";
import styles from "./workbench.module.css";

export const metadata = {
  title: "Case workbench",
};

function isCaseIdentifier(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export default async function CaseWorkbenchPage({
  params,
}: Readonly<{ params: Promise<{ caseId: string }> }>) {
  const { caseId } = await params;
  if (!isCaseIdentifier(caseId)) notFound();

  let detail: CaseDetailResponse | undefined;
  let artifactStatuses: readonly ArtifactStatusResponse[] = [];
  try {
    const api = await getAuthenticatedCaseApi();
    const [caseDetail, artifactStatusResponse] = await Promise.all([
      api.getCase(caseId),
      api.listCaseArtifactStatuses(caseId),
    ]);
    detail = caseDetail;
    artifactStatuses = artifactStatusResponse.items;
  } catch (error) {
    const status = typeof error === "object" && error !== null && "status" in error
      ? error.status
      : undefined;

    if (status === 404) notFound();

  }

  if (detail === undefined) {
    return (
      <section aria-labelledby="case-unavailable" className={styles.workbench}>
        <div className={styles.errorState} role="alert">
          <p className={styles.eyebrow}>Case workbench unavailable</p>
          <h1 id="case-unavailable">The case could not be loaded.</h1>
          <p>
            No case content has been shown. Confirm your verified session and organization scope, then try again.
          </p>
          <Link className="button button-primary" href="/cases">
            Return to case queue
          </Link>
        </div>
      </section>
    );
  }

  const session = await getAuthenticatedSession();
  return (
    <CaseWorkbench
      artifactStatuses={artifactStatuses}
      canWriteCases={session !== null && canWriteCases(session)}
      detail={detail}
    />
  );
}
