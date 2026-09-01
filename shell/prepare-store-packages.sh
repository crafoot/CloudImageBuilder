#!/bin/bash
set -euo pipefail

if [[ "$#" -ne 5 ]]; then
  echo "Usage: $0 BUNDLE TARGET_DIR EXPECTED_SHA256 ARCHIVE_OFFSET ARCHIVE_SIZE"
  exit 2
fi

bundle="$1"
target_dir="$2"
expected_sha256="$3"
archive_offset="$4"
archive_size="$5"

actual_sha256="$(sha256sum "$bundle" | awk '{print $1}')"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
  echo "Store bundle checksum mismatch"
  exit 1
fi

extract_dir="$(mktemp -d)"
trap 'rm -rf "$extract_dir"' EXIT

dd if="$bundle" bs=1 skip="$archive_offset" count="$archive_size" 2>/dev/null \
  | gzip -dc \
  | tar -xf - -C "$extract_dir"

expected_packages=(
  luci-app-store-0.2.0-r3.apk
  luci-lib-taskd-1.0.25.apk
  luci-lib-xterm-4.18.0.apk
  taskd-1.0.3-r2.apk
)

extracted_apks=()
while IFS= read -r package_path; do
  extracted_apks+=("$package_path")
done < <(find "$extract_dir" -type f -name '*.apk' -print)
if [[ "${#extracted_apks[@]}" -ne "${#expected_packages[@]}" ]]; then
  echo "Expected exactly four store APKs, found ${#extracted_apks[@]}"
  exit 1
fi

mkdir -p "$target_dir"
for package in "${expected_packages[@]}"; do
  matches=()
  while IFS= read -r package_path; do
    matches+=("$package_path")
  done < <(find "$extract_dir" -type f -name "$package" -print)
  if [[ "${#matches[@]}" -ne 1 ]]; then
    echo "Expected exactly one $package, found ${#matches[@]}"
    exit 1
  fi
  cp "${matches[0]}" "$target_dir/$package"
done

echo "Verified and staged pinned iStore packages"
