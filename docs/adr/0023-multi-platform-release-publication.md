# ADR-0023: Scan platform images before publishing a multi-platform release

- Status: Accepted
- Date: 2026-08-10

## Context

`v0.5.0` established a fail-closed release boundary: the production container is built and
scanned before the release job receives GHCR credentials. P1.7d made the Dockerfile portable
across `linux/amd64` and `linux/arm64`, but the publication workflow still produced only the
runner-native image.

A naive migration to `docker buildx build --platform linux/amd64,linux/arm64 --push` would publish
container bytes before the existing Syft/Grype policy could approve both architecture-specific
artifacts. Building local images for scanning and rebuilding after authentication would also weaken
the evidence binding because the scanned bytes and published bytes would be separate builds.

## Decision

For each release tag:

1. configure QEMU and Buildx using SHA-pinned GitHub Actions;
2. build `linux/amd64` and `linux/arm64` as two local single-platform images;
3. generate a CycloneDX SBOM and complete Grype report for each local image;
4. apply the vulnerability policy independently to both reports;
5. authenticate to GHCR only after both policies pass;
6. push those exact scanned local images under immutable version/commit platform tags;
7. resolve and compare their registry digests;
8. create the version and commit OCI indexes from the two canonical platform digests;
9. require both index tags to resolve to the same digest;
10. validate that the published index contains exactly the expected `linux/amd64` and
    `linux/arm64` descriptors with the scanned digests;
11. attest the final index provenance and attach each platform's CycloneDX SBOM to its own
    platform digest.

Architecture-specific tags are intentionally retained:

```text
vX.Y.Z-amd64
vX.Y.Z-arm64
sha-<commit>-amd64
sha-<commit>-arm64
```

They are immutable evidence/discovery aliases. Normal consumers should use `vX.Y.Z` or the final
index digest.

## Consequences

### Positive

- Apple Silicon receives a native ARM64 image.
- Windows Docker Desktop and x86_64 Linux continue using AMD64.
- No registry authentication occurs before both architecture policies pass.
- Published platform manifests are the same local image subjects that were scanned.
- The release bundle preserves independent SBOM, vulnerability, and policy evidence for both
  architectures.
- `image-platforms.json` cryptographically binds the final index to both platform digests.

### Trade-offs

- Release time increases because ARM64 is built under QEMU on an AMD64 GitHub-hosted runner.
- Evidence size increases because image SBOMs and vulnerability reports are duplicated per
  platform.
- Four architecture-specific immutable tags are retained in addition to the version and commit
  index tags.
- A failure after platform pushes but before index creation remains a partial publication. As with
  v0.5.0, the version must not be reused; prepare a new version.

## Alternatives rejected

### Push a multi-platform Buildx build before scanning

Rejected because registry publication would precede vulnerability-policy approval.

### Scan only the AMD64 image

Rejected because OS packages and architecture-specific artifacts can differ between manifests.

### Scan local images and rebuild for publication

Rejected because the published bytes would not be the exact scanned subjects.

### Publish only architecture-specific tags

Rejected because consumers should have one standard multi-platform version reference.

## Verification

The executable supply-chain validator requires the two platform builds, per-platform evidence,
policy-before-login ordering, immutable platform tags, two `imagetools create` operations, exact
platform evidence, and per-platform SBOM attestations.
