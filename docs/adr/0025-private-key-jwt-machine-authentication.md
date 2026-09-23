# ADR-0025: Asymmetric machine/client authentication using OAuth `private_key_jwt`

- Status: Accepted
- Date: 2026-09-22
- Builds on: ADR-0018, ADR-0024

## Context

The Client Credentials profile (ADR-0018) authenticated the machine client only with
`client_secret_basic`. A shared secret sits on both sides of the trust boundary: the authorization
server has to store something that can impersonate the client, and every replayable request
carries it. ADR-0018 named JWT client assertions as the next hardening step.

MCP Python SDK 2.2 ships `PrivateKeyJWTOAuthProvider` (RFC 7523 section 2.2 client
authentication for the `client_credentials` grant), plus `SignedJWTParameters`, which builds an
SDK-signed assertion callback. The SDK sets the assertion `aud` to the **authorization server's
issuer identifier**, following draft-ietf-oauth-rfc7523bis. With `issuer=` configured, the SDK
signs an assertion only after metadata for exactly that issuer has been discovered.

This profile is asymmetric client authentication with a key the operator provisions and
registers. It is **not** Workload Identity Federation: nothing here exchanges a platform-issued
workload token (Kubernetes, cloud IAM, SPIFFE) for credentials. Using `private_key_jwt` keeps the
architecture ready for such a profile later, but the two are not equivalent. Federation stays a
separate roadmap item.

## Decision

1. Add `MCP_CLIENT_CLIENT_AUTH_METHOD=client_secret_basic|private_key_jwt` for
   `auth_mode=client_credentials`. The default stays `client_secret_basic`, so existing
   deployments behave the same.
2. `private_key_jwt` uses the SDK only: `SignedJWTParameters(issuer=client_id, subject=client_id,
   signing_key=…, signing_algorithm=…, lifetime_seconds=60).create_assertion_provider()`, passed
   to `PrivateKeyJWTOAuthProvider(..., issuer=<configured issuer>)`. The client has no JWT
   signing or token-request code of its own.
   - `iss` = `sub` = the configured client ID. Each assertion carries a fresh `jti`.
   - `aud` = the authorization server's issuer, the audience that RFC 7523bis-compliant servers
     require. The assertion is also effectively bound to one token endpoint: the SDK sends it
     only to the token endpoint of metadata whose issuer matches the configured one (ADR-0024).
     We deliberately do not substitute the token endpoint URL as `aud`, because rfc7523bis
     servers reject that.
   - Validity is 60 seconds, shorter than the SDK's 300-second default.
3. The key is read from a file only, via `MCP_CLIENT_CLIENT_CREDENTIALS_PRIVATE_KEY_PATH`. There
   is no environment variable that carries PEM material. The loader applies an SSH
   `StrictModes`-style policy and fails closed:
   - no symbolic link in any path component (`lstat`, then `O_NOFOLLOW` relative to the
     already-opened parent, then a device/inode match);
   - directories owned by the current user or root and not group/world-writable;
   - the key must be a regular file with a single link, owned by the current user or root, with
     no group/other permission bits, and at most 64 KiB;
   - it must be an unencrypted RSA key of at least 2048 bits (RS256) or a P-256 EC key (ES256).
     The algorithm is derived from the key, so there is no algorithm setting.
4. The PEM is only ever held as a `SecretStr` until it is handed to the SDK callback. It is never
   logged, never written to spans, and never stored in `TokenStorage`: machine mode keeps only
   access tokens, in memory. Loader errors never echo file content. Preflight reports only
   `private_key_rejected`.
5. `client_secret_basic` and `private_key_jwt` are mutually exclusive. Machine-only settings are
   rejected in interactive mode.

## Consequences

- An authorization server stores only the client's public key. A leaked assertion is valid for
  at most 60 seconds, only for one issuer audience, and a replay-checking server will not accept
  its `jti` twice.
- Real-wire tests verify the SDK-signed assertion with the public key (audience, issuer,
  subject, lifetime, `jti`). They show that no assertion is signed when Protected Resource
  Metadata substitutes another authorization server, and that the PEM appears in no request,
  log, span, exception, or stored token.
- **Kubernetes trade-off.** Standard Kubernetes Secret volumes present each key as a symlink
  (`key -> ..data/key`), so the loader rejects them. A `subPath` mount of the key file presents a
  regular file and works, **but Kubernetes does not update `subPath` mounts when the Secret
  changes**. Rotating the key then requires restarting or redeploying the pod. We accept this
  explicitly rather than relaxing the symlink rule. A CSI secret-store driver or an init
  container that copies the key into an `emptyDir` are the alternatives when automatic rotation
  matters. Docker/Compose secrets are regular files and work unchanged.
- The SDK's `SignedJWTParameters` emits no `kid` or `x5t` JOSE header. Authorization servers that
  select the verification key by header need a single registered key for this client. Microsoft
  Entra certificate credentials require `x5t`, so this profile remains generic-OIDC only.
- The `cryptography` dependency, already present through the SDK's `pyjwt[crypto]`, is declared
  explicitly because the loader imports it. No new package enters the lockfile.
