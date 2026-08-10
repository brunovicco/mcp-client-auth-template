# Supply-chain trust baseline

This document defines the P1.6 controls for dependency, CI, software inventory, artifact
provenance, and release integrity. It is a project policy and review aid, not a certification.

## Threat model and controls

| Threat | P1.6 control | Residual risk |
| --- | --- | --- |
| A mutable or compromised GitHub Action executes in CI | Every third-party action is pinned to a full commit SHA; the local quality gate rejects mutable refs | A trusted pinned commit may itself contain a defect or compromise |
| A workflow token has more authority than its job needs | Every workflow declares explicit permissions; release, registry, and attestation writes are isolated by job | GitHub-hosted runner, GitHub OIDC, and platform trust remain |
| A vulnerable dependency enters through a routine update | Dependency Review blocks recognized new high/critical advisories; `pip-audit` checks the locked environment | Ecosystem coverage and advisory databases can lag a newly disclosed issue |
| Dependencies become stale | Dependabot checks Python/uv and GitHub Actions weekly with bounded PR volume | Maintainers must still review and merge safe updates |
| A dependency creates incompatible licensing obligations | Dependency Review denies new AGPL-3.0-only and GPL-3.0-only packages; all new licenses are reviewed | Automated license detection can be incomplete or wrong |
| Automation introduces a breaking update | Minor/patch updates are grouped, major updates remain isolated, and no update is auto-merged | CI cannot prove every downstream integration |

## Dependency acceptance policy

A direct dependency must have a documented product or engineering need, an actively maintained
upstream, a license compatible with the intended distribution, and no simpler dependency already
providing the capability. Reviewers inspect manifest and `uv.lock` changes, release notes, changed
transitive dependencies, CI results, and relevant advisories. A green Dependabot PR is evidence,
not automatic approval.

The repository license does not relicense dependencies. GPL-2.0 and GPL-3.0, including
"or-later" variants, and AGPL-3.0 additions are denied by default because their reciprocal
obligations do not match this template's intended reuse model. Other licenses still require human
review; unknown or ambiguous license data must be resolved before merge.

## GitHub Actions policy

- Pin remote actions and reusable workflows to a full 40-character commit SHA, retain a release
  comment for maintainability, and disable persisted checkout credentials.
- Pin container actions by SHA-256 digest. Local actions may use a repository-relative path.
- Keep workflows read-only by default. In the tag-triggered release workflow, the Python build may
  write attestations, the container job may write GHCR plus attestations, and the final job may
  write GitHub Release contents. No job receives all three authorities. Any further write requires
  a documented threat-model update and executable policy changes in the same PR.
- Do not expose repository secrets to untrusted pull-request code. No supply-chain update is
  auto-merged.
- Review an action update like executable code: verify its upstream release and inspect the commit
  range before accepting the new SHA.

## Update and exception workflow

Dependabot checks the `uv` and `github-actions` ecosystems every Monday. Minor and patch updates
are grouped to control noise; major upgrades remain separate. All updates must pass the full
quality, compatibility, and applicable E2E gates.

An exception requires a reviewable ADR that names the owner, affected dependency or workflow,
business need, compensating controls, expiry or review date, and removal plan. Exceptions must not
contain secrets, tokens, personal data, or private advisory details.

Repository administrators should keep the dependency graph, Dependabot alerts, and Dependabot
security updates enabled. These settings complement the version-update configuration committed in
`.github/dependabot.yml`.

## SBOM and vulnerability evidence (P1.6b)

The `supply-chain-evidence` workflow builds two CycloneDX JSON inventories with Syft: a source view
from the committed `uv.lock`, including the declared dependency graph, and a runtime view from the
final container image, including installed Python and operating-system packages. Grype records the
complete image vulnerability report and fails the workflow for high or critical findings that have
an available fix. Unfixed findings remain visible in the complete artifact instead of disappearing
from the evidence.

The gate evaluates the same saved report against `security/vulnerability-exceptions.json`. An
exception must match the exact advisory namespace, package type, and installed version; identify an
owner, review date, expiry, rationale, and removal plan; and span no more than 90 days. Expired,
stale, duplicate, or version-drifted exceptions fail closed. The current CPython exceptions exist
only because Grype lists fixes outside the supported stable runtime lines, expire on 2026-09-30,
and remain present in the complete report. [ADR-0020](adr/0020-actionable-vulnerability-exceptions.md)
records the decision and follow-up.

Syft and Grype are downloaded only from exact immutable release URLs. Their versions and per-platform
SHA-256 checksums are committed in `scripts/install_security_tools.sh`; a checksum mismatch stops the
workflow before either binary executes. The artifact is retained for 14 days and contains the two
SBOMs, complete Grype report, and minimized policy result. It contains no credentials, source
content, request data, tokens, prompts, or MCP payloads.

The pull-request/main P1.6b evidence remains an Actions artifact. Tag publication regenerates and
revalidates the same evidence before publishing the release image.

## Reproducible release artifacts and provenance (P1.6c)

The `secure-release-publication` workflow runs only after a `v*` tag is pushed. It requires the tag
to equal the version in `pyproject.toml`, builds the wheel and source distribution twice with a
commit-derived `SOURCE_DATE_EPOCH`, and requires both sets to be byte-for-byte identical. The
release validator checks archive paths, package metadata, expected filenames, and a strict content
boundary before copying the artifacts and writing `SHA256SUMS`.

The Hatchling backend is exact-pinned in `pyproject.toml`; its isolated transitive build
environment is exact-pinned in `build-constraints.txt` and passed to both `uv build` executions.
This prevents a later backend/dependency resolution from silently changing the bytes for the same
tag.

Hatch's default sdist selection can include any file not ignored by the local VCS. The explicit
`only-include` configuration prevents local assistant configuration, worktrees, credentials, and
unrelated repository automation from entering a source release. The archive validator independently
enforces the same boundary so configuration drift fails closed.

After validation, the SHA-256 subjects receive GitHub/Sigstore build-provenance attestations via a
SHA-pinned `actions/attest`. The Python job can mint an OIDC identity and write attestations and
artifact metadata, but cannot write repository contents, packages, releases, or registries.

```bash
sha256sum --check SHA256SUMS
gh attestation verify mcp_client_auth_template-<version>-py3-none-any.whl \
  --repo brunovicco/mcp-client-auth-template
```

## Secure release publication and container provenance (P1.6d)

The same tag workflow now separates three authorities. `build-python-artifacts` creates and attests
the reproducible packages. `publish-container` builds the production image locally, generates
source/image CycloneDX SBOMs, records the complete Grype report, and applies the fail-closed
exception policy before it receives a GHCR token. `publish-github-release` receives only
`contents: write` and runs only after the other jobs succeed.

The approved local image is tagged with the version and full commit, then pushed once. The workflow
refuses to overwrite either tag, records the resulting `ghcr.io/...@sha256:...` subject, and creates
both build-provenance and CycloneDX SBOM attestations for that digest in GHCR. Tags are discovery
aliases; deployment and verification must use the digest from `image-digest.txt`.

The GitHub Release contains the wheel, sdist, package checksum manifest, source/image SBOMs,
complete Grype report, policy result, image subject, `release-manifest.json`, and
`RELEASE_SHA256SUMS`. The final validator accepts only that file set, rechecks the package archives
and checksums, validates evidence identities and policy status, and binds the tag, source commit,
repository, and image digest before `gh release create --verify-tag` runs. PyPI publication is not
part of P1.6d.

GHCR package visibility is an administrator-controlled setting, not a workflow permission. After
the first publication, mark the linked container package public if anonymous consumption is
intended; until then, `docker pull` and OCI attestation verification require GHCR authentication.
The workflow deliberately does not mutate package visibility.

Verify a release before use:

```bash
gh release download v<version> \
  --repo brunovicco/mcp-client-auth-template \
  --dir release
(cd release && sha256sum --check RELEASE_SHA256SUMS)
gh attestation verify release/mcp_client_auth_template-<version>-py3-none-any.whl \
  --repo brunovicco/mcp-client-auth-template
image="$(cat release/image-digest.txt)"
gh attestation verify "oci://${image}" \
  --repo brunovicco/mcp-client-auth-template
docker pull "$image"
```

For a coordinated server/client version, publish and verify the server first. Publish the client
tag only after the server assets and digest pass verification. This ordering avoids announcing a
client whose companion server release is incomplete; it creates no cross-repository CI dependency.
[ADR-0022](adr/0022-secure-release-publication.md) records the authority split, residual failure
mode, and release ceremony.

## Executable evidence

Run the focused validator with:

```bash
uv run python scripts/quality_gate.py --check supply-chain
```

The complete definition of done remains:

```bash
uv run python scripts/quality_gate.py
```
