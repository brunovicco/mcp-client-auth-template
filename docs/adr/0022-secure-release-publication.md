# ADR-0022: Isolate secure release publication by authority and immutable subject

- Status: Accepted
- Date: 2026-08-09

## Context

P1.6c produces byte-reproducible Python artifacts, a checksum manifest, and GitHub build-provenance
attestations from a tag-only workflow. It intentionally cannot create a GitHub Release or publish a
container. P1.6d must make the validated artifacts durable and consumable, bind software inventories
to the shipped subjects, and publish the production image without giving one broad workflow token
every write capability.

A release workflow necessarily introduces two new authorities: `packages: write` for GHCR and
`contents: write` for GitHub Releases. The existing source/image SBOM and Grype gate also ran on
pull requests and `main`, but did not prove that the scanned image was the image later published.

## Decision

- Keep one `v*` tag entry point and serialize runs for the same tag without cancelling an active
  publication.
- Split the workflow into three jobs with explicit authority:
  - `build-python-artifacts` can mint attestations but cannot publish packages or releases;
  - `publish-container` can write GHCR and attestations but cannot mutate repository contents;
  - `publish-github-release` can create the release but has no package or OIDC authority.
- Continue building wheel and sdist twice in the exact constrained environment. Generate build
  provenance for their checksum subjects.
- Build the production image locally, generate source and image CycloneDX inventories, record the
  complete Grype report, and enforce the reviewed vulnerability policy before authenticating to
  GHCR. Publish that same approved local image under the version tag and a full-commit tag.
- Refuse to overwrite either image tag. Record the resulting
  `ghcr.io/<owner>/<repository>@sha256:<digest>` subject and treat that digest, not either tag, as
  the deployable identity.
- Generate a build-provenance attestation and CycloneDX SBOM attestation for the image digest in
  GHCR. Bind the source CycloneDX SBOM to the wheel and sdist checksum subjects.
- Disable linked-artifact storage records because these repositories are user-owned; the registry
  attestations and GitHub attestation records remain the verification sources.
- Assemble the GitHub Release only after both producer jobs pass. Revalidate exact file sets,
  package checksums and archives, SBOM identities, Grype identity, vulnerability-policy status,
  source commit, tag/version, and image digest before creating the release.
- Publish wheel, sdist, package checksums, source/image SBOMs, the complete vulnerability report,
  policy result, immutable image subject, a machine-readable release manifest, and a checksum
  manifest covering every release asset except itself.
- Do not publish to PyPI in P1.6d. A package-index trust model, credentials, and recovery procedure
  require a separate decision.
- Release the coordinated pair server-first: publish and verify the server release/image, then
  publish and verify the client at the same version. Repository merges remain independent.

## Alternatives considered

- Give one job `contents`, `packages`, OIDC, and attestation writes. Rejected because compromise
  of any build step would receive every publication capability.
- Push first and scan the registry image afterward. Rejected because a vulnerable image would
  become publicly consumable before the blocking policy runs.
- Rebuild the container after scanning. Rejected because the published bytes would no longer be the
  evaluated subject.
- Sign version tags without recording a digest. Rejected because GHCR tags are mutable references;
  consumers need an immutable subject.
- Publish only SBOM files as release assets. Rejected because an unattached inventory can be
  substituted independently of the artifact it describes.
- Publish to PyPI in the same increment. Deferred to avoid introducing a third credential and
  recovery boundary.

## Consequences

A successful tag run produces durable GitHub Release assets and a GHCR image that consumers can
verify by checksum, source workflow/commit, and immutable digest. Write authority is narrow and
visible in executable policy. The release is intentionally all-or-nothing at the GitHub Release
layer: it is created only after package, evidence, image, and attestation work succeeds.

GHCR necessarily receives the image before registry attestations can be attached. If a later
attestation or release step fails, the image digest can exist without a GitHub Release. The workflow
will not overwrite its tags on retry; maintainers must investigate and either reconcile/remove the
failed unpublished package version or issue a new version. This favors immutability over silent
recovery.

## Security and privacy impact

The registry token exists only after the vulnerability gate passes and is removed from Docker
configuration in an always-run step. The final release job never receives package or OIDC authority.
Published evidence contains dependency, image-package, public vulnerability, digest, workflow,
repository, commit, and version metadata. It contains no OAuth material, access/refresh tokens,
authorization codes, PKCE values, client secrets, JWTs, MCP payloads, prompts, request data, or
assistant-local files.

The design still trusts GitHub-hosted runners, GitHub OIDC/Sigstore, GHCR, the pinned Actions and
security-tool releases, and repository administrators' tag protection. Attestations provide value
only when consumers verify identity and digest.

## Operational impact

Maintainers should protect `v*` tags and optionally place the final job behind a GitHub
`release` environment with required reviewers. GHCR package visibility remains an administrator
setting. After the first successful publish,
maintainers must explicitly make the linked package public if anonymous pulls are intended; the
workflow does not broaden package visibility.

A coordinated release ceremony is:

1. complete the final main audit and choose one version for both repositories;
2. push the server tag, then verify checksums, attestations, SBOMs, and GHCR digest;
3. push the client tag only after the server is verified;
4. verify the client assets/image and the cross-repository compatibility evidence;
5. announce the pair using digest-qualified image references.

## Follow-up

- Exercise the workflow with the next planned version; do not move or recreate an existing tag.
- Decide separately whether PyPI trusted publishing belongs in these reference templates.
- Evaluate repository rulesets for protected tags and a reviewer-gated release environment.
