# Supply-chain trust baseline

This document defines the P1.6a controls for dependency and CI trust. It is a project policy and
review aid, not a certification. SBOM generation, artifact attestations, release signing, and
container provenance are intentionally deferred to later P1.6 increments.

## Threat model and controls

| Threat | P1.6a control | Residual risk |
| --- | --- | --- |
| A mutable or compromised GitHub Action executes in CI | Every third-party action is pinned to a full commit SHA; the local quality gate rejects mutable refs | A trusted pinned commit may itself contain a defect or compromise |
| A workflow token has more authority than its job needs | Every workflow declares explicit permissions; the P1.6a validator rejects write access | GitHub-hosted runner and platform trust remain |
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
- Keep workflow permissions read-only in P1.6a. A future write permission requires a narrowly
  scoped job, a documented threat-model update, and executable policy changes in the same PR.
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

## Executable evidence

Run the focused validator with:

```bash
uv run python scripts/quality_gate.py --check supply-chain
```

The complete definition of done remains:

```bash
uv run python scripts/quality_gate.py
```
