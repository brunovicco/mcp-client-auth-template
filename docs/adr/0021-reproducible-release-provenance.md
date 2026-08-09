# ADR-0021: Build allowlisted reproducible Python artifacts before attestation

- Status: Accepted
- Date: 2026-08-09

## Context

The repository had no automated release-artifact build. A local audit showed that Hatch's default
sdist selection included files outside the Python package, including untracked `CLAUDE.md` and
local `.claude/` worktrees. The builds were byte-reproducible, but attesting them in that state
would provide strong provenance for an incorrectly scoped artifact.

P1.6c also needs a narrow exception to the read-only workflow policy because GitHub artifact
attestations require an OIDC identity and attestation write permissions.

## Decision

- Define an explicit Hatch sdist `only-include` list for package source, changelog, license, and the
  Portuguese README. Hatch continues to force the build metadata, primary README, and license files.
- Pin Hatchling exactly in `pyproject.toml` and its isolated transitive environment in
  `build-constraints.txt`; apply those constraints to both builds.
- On `v*` tag pushes, require `v<project.version>`, derive `SOURCE_DATE_EPOCH` from the tagged commit,
  and build the wheel and sdist twice in isolated output directories.
- Validate identical filenames and SHA-256 digests, package Name/Version metadata, safe archive
  paths, regular-file-only sdists, and exact wheel/sdist content boundaries.
- Publish a GNU-compatible `SHA256SUMS` manifest and attest its wheel/sdist subjects with the
  SHA-pinned `actions/attest` release recommended for new GitHub implementations.
- Limit the job to `contents: read`, `id-token: write`, `attestations: write`, and
  `artifact-metadata: write`. Retain artifacts for 30 days.
- Do not grant `contents: write` or `packages: write`; do not create releases or publish packages or
  images in P1.6c.

## Alternatives considered

- Attest the existing default Hatch build. Rejected because provenance does not correct accidental
  local-file inclusion.
- Add `.claude/` and `CLAUDE.md` to `.gitignore` only. Rejected because a denylist cannot constrain
  other unknown local files and does not express the intended release contents.
- Build once and trust Hatch's reproducible default. Rejected because a second build provides
  executable evidence that timestamps or environment-dependent inputs did not change the bytes.
- Publish release assets and a container image in the same workflow. Deferred to keep registry and
  repository write authority out of this increment.

## Consequences

Tagged Python artifacts have a small, reviewable content set, deterministic bytes, published
checksums, and verifiable build provenance tied to the repository, workflow, commit, and tag. New
files intended for the sdist require an explicit policy change.

## Security and privacy impact

The allowlist and archive validator prevent local assistant state, worktrees, tokens, credentials,
and unrelated automation from being packaged accidentally. Attestations contain artifact identity,
digest, source, and build metadata; they contain no MCP payloads, OAuth material, application data,
or artifact contents. The signing identity is short-lived and available only to the tag job.

## Operational impact

A release tag fails if it does not match project metadata, if either build differs, if archive
contents drift, or if GitHub cannot issue/store the attestation. Consumers can verify checksums
offline and provenance with `gh attestation verify`. P1.6c artifacts expire from Actions after 30
days unless P1.6d later attaches them to a release.

## Follow-up

- In P1.6d, attach the validated artifacts, checksums, and SBOMs to a deliberate GitHub Release.
- Add SBOM attestations and publish the container to GHCR by immutable digest with provenance.
- Document the final coordinated server/client release order and verification ceremony.
