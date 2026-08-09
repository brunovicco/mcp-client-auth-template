#!/usr/bin/env bash
set -euo pipefail

readonly SYFT_VERSION="1.50.0"
readonly GRYPE_VERSION="0.116.1"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 DESTINATION" >&2
  exit 2
fi

destination="$1"
mkdir -p "$destination"

case "$(uname -s)/$(uname -m)" in
  Darwin/x86_64)
    platform="darwin_amd64"
    syft_checksum="d11a8c7bc27114853bd7c1e1b2f3be3ddda3a1de17aee585329f04c369341c75"
    grype_checksum="e5ff3adac317511876de7863598587a7dbab0c47c8e150368b7df06909c11f4e"
    ;;
  Darwin/arm64)
    platform="darwin_arm64"
    syft_checksum="e32fdb9d47823fa633748a1efca2528fd77c37469ea93c9e40ab835da44e4cce"
    grype_checksum="f493f169cbaae48bade169532b20235fc16653d2a044a5bc6fe6f69a3923f975"
    ;;
  Linux/x86_64)
    platform="linux_amd64"
    syft_checksum="bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788"
    grype_checksum="0122df7b655981abe547ad3d2190d65551dac6a2bfc80b4dc2a989b5d0587458"
    ;;
  Linux/aarch64 | Linux/arm64)
    platform="linux_arm64"
    syft_checksum="887c57cbcc2d0e8c5c110a4571a3fc7150058b24d74f993ee4663516e5c8ce86"
    grype_checksum="a8d7504a149629324eb5f4ce3dc25dfd211bbfe047e64ee2bf7844b466c3d84d"
    ;;
  *)
    echo "unsupported security-tool platform: $(uname -s)/$(uname -m)" >&2
    exit 2
    ;;
esac

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/mcp-security-tools.XXXXXX")"
trap 'rm -rf -- "$temporary_directory"' EXIT

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    shasum -a 256 "$path" | awk '{print $1}'
  fi
}

install_tool() {
  local tool="$1"
  local version="$2"
  local expected_checksum="$3"
  local archive="${tool}_${version}_${platform}.tar.gz"
  local archive_path="${temporary_directory}/${archive}"
  local url="https://github.com/anchore/${tool}/releases/download/v${version}/${archive}"
  local actual_checksum

  curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    --location \
    --connect-timeout 10 \
    --max-time 120 \
    --retry 2 \
    --retry-all-errors \
    --output "$archive_path" \
    "$url"

  actual_checksum="$(sha256_file "$archive_path")"
  if [[ "$actual_checksum" != "$expected_checksum" ]]; then
    echo "checksum verification failed for ${archive}" >&2
    exit 1
  fi

  tar -xzf "$archive_path" -C "$destination" "$tool"
  chmod 0755 "${destination}/${tool}"
}

install_tool "syft" "$SYFT_VERSION" "$syft_checksum"
install_tool "grype" "$GRYPE_VERSION" "$grype_checksum"

"${destination}/syft" version
"${destination}/grype" version
