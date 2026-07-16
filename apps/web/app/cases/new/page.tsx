import { getAuthenticatedSession, canWriteCases } from "@/lib/auth/session";

import { NewCaseIntake } from "./new-case-intake";

export const metadata = {
  title: "New case",
};

type SearchParameters = { intake_error?: string | string[] };

export default async function NewCasePage({
  searchParams,
}: {
  searchParams: Promise<SearchParameters>;
}) {
  const session = await getAuthenticatedSession();
  const parameters = await searchParams;
  const intakeError = typeof parameters.intake_error === "string" ? parameters.intake_error : undefined;

  if (session === null || !canWriteCases(session)) {
    return (
      <section className="content-frame" aria-labelledby="case-create-forbidden">
        <p className="eyebrow">READ-ONLY ROLE</p>
        <h1 id="case-create-forbidden">You can view cases, but cannot create one.</h1>
        <p>Your verified role does not include the case-write permission.</p>
      </section>
    );
  }

  return <NewCaseIntake initialError={intakeError} />;
}
