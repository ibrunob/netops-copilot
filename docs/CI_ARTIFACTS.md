# CI image and SBOM artifacts

The `image-sbom` CI job builds the API and web Dockerfiles from the checked-out
commit. The images are loaded only into the ephemeral GitHub Actions Docker
daemon and are never pushed to a registry. For each image, Anchore Syft produces
an SPDX JSON SBOM and CI uploads it as a workflow artifact named
`sbom-api-spdx` or `sbom-web-spdx` with a 14-day retention period.

These SBOMs describe the tested CI image inputs. They are not release assets,
container attestations, signatures, or proof that an image was published.

## Protected GHCR promotion

[`promote-images.yml`](../.github/workflows/promote-images.yml) is a manual,
main-branch-only promotion workflow. It pushes immutable
`ghcr.io/<owner>/<repository>/netops-api:sha-<commit>` and `netops-web` images,
creates a GitHub provenance attestation, then keylessly signs and verifies each
published digest with GitHub Actions OIDC and Sigstore.

The workflow is deliberately inert until the repository owner configures the
following controls:

1. Create a protected GitHub `production` environment with the required
   reviewers and deployment/rollback policy.
2. Ensure Actions can publish to GitHub Packages for this repository, then set
   the repository variable `NETOPS_RELEASE_ENABLED` to `true` only after the
   environment protection is active.
3. Define registry retention, vulnerability-response, and SBOM/attestation
   retention requirements.

Consumers deploy the immutable digest, never a mutable tag, and verify it with
the repository-specific workflow identity:

```sh
cosign verify \
  --certificate-identity "https://github.com/OWNER/REPOSITORY/.github/workflows/promote-images.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/OWNER/REPOSITORY/netops-api@sha256:DIGEST
```
