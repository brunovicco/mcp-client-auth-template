# ADR-0007: Fail-closed local OAuth token storage

- Status: Accepted
- Date: 2026-08-08

## Context

The native client optionally persists OAuth access tokens, refresh tokens, and SDK client
registration state so every invocation does not require a new browser authorization. These are
bearer credentials: disclosure of an unexpired token can let another process act with the token's
privileges, while corruption or replacement of the file can change which authorization-server
registration the SDK reloads.

The original `FileTokenStorage` created its parent directory with the process umask, wrote the
final pathname directly, and applied mode `0600` only after the write. It also used ordinary
`Path.exists()`/`read_text()` calls, which follow symbolic links. That was adequate for a compact
demo but not a defensible reference implementation.

RFC 9700 recommends reducing the consequences of stolen access and refresh tokens and requires
strong replay protections for refresh tokens issued to public clients. Local storage cannot create
sender-constrained tokens or refresh-token rotation -- those remain authorization-server
responsibilities -- but it must not add avoidable filesystem disclosure or replacement paths.

## Decision

`FileTokenStorage` remains a deliberately dependency-free plaintext option for one local user, but
its filesystem contract is now fail-closed:

- supported only on POSIX systems, where the adapter can enforce ownership, `dir_fd`, link, and
  durability semantics consistently;
- normalize the configured path without resolving links and traverse every directory component
  relative to an already-open directory descriptor with `O_NOFOLLOW`, rejecting symlinks and
  inode swaps without relying on platform-specific `O_NOFOLLOW_ANY`;
- create a new dedicated parent directory with mode `0700`; reject an existing parent unless it is
  a real directory owned by the current uid and already mode `0700`;
- access the token file relative to an opened directory descriptor;
- reject a final symbolic link, non-regular file, wrong owner, hard-linked file, or mode other than
  `0600` before reading or replacing it;
- bound the JSON file to 1 MiB and reject malformed UTF-8/JSON or a non-object root rather than
  silently treating corruption as an empty store;
- write a same-directory uniquely named temporary file using `O_CREAT|O_EXCL`, force it to `0600`,
  write the complete payload, and `fsync` the file;
- re-check any existing destination immediately before replacement, use atomic `os.replace`, then
  `fsync` the containing directory; and
- remove an uncommitted temporary file on failure so a failed write leaves the previous token file
  intact.

The adapter does **not** transparently chmod a pre-existing permissive directory or file. A caller
may have deliberately pointed the setting at an unrelated directory, and silently tightening that
directory could break other applications. Existing users should explicitly run `chmod 700` on the
storage directory and `chmod 600` on the token file if migration is required.

## Consequences

- The default `~/.mcp-client-auth-template/tokens.json` remains usable after its directory has the
  required `0700` mode. New directories are created correctly from the start.
- A Windows deployment must use `InMemoryTokenStorage` or provide an OS-native keyring/credential
  adapter. The template prefers an explicit unsupported-platform failure over presenting weaker
  filesystem semantics as equivalent protection.
- Plaintext at rest remains a conscious limitation. POSIX permissions and atomicity reduce local
  exposure and corruption risk but are not encryption. Higher-assurance deployments should replace
  this adapter with OS keychain/keyring storage or a secrets manager.
- Concurrent independent client processes may still race at the logical read-modify-write level;
  atomic replacement prevents torn JSON, but cross-process field merging is outside this adapter's
  single-user CLI scope.
