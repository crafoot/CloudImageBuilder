#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

payload_dir="$work_dir/payload"
target_dir="$work_dir/target"
mkdir -p "$payload_dir" "$target_dir"

for package in \
  luci-app-store-0.2.0-r3.apk \
  luci-lib-taskd-1.0.25.apk \
  luci-lib-xterm-4.18.0.apk \
  taskd-1.0.3-r2.apk; do
  printf 'fixture for %s\n' "$package" > "$payload_dir/$package"
done
printf '#!/bin/sh\nexit 99\n' > "$payload_dir/install25.sh"

tar -czf "$work_dir/payload.tar.gz" -C "$payload_dir" .
header_size=128
payload_size="$(wc -c < "$work_dir/payload.tar.gz" | tr -d ' ')"
dd if=/dev/zero of="$work_dir/store.run" bs=1 count="$header_size" 2>/dev/null
cat "$work_dir/payload.tar.gz" >> "$work_dir/store.run"
expected_sha="$(sha256sum "$work_dir/store.run" | awk '{print $1}')"

"$repo_root/shell/prepare-store-packages.sh" \
  "$work_dir/store.run" \
  "$target_dir" \
  "$expected_sha" \
  "$header_size" \
  "$payload_size"

actual_files="$(find "$target_dir" -maxdepth 1 -type f | sed 's#.*/##' | sort)"
expected_files="$(printf '%s\n' \
  luci-app-store-0.2.0-r3.apk \
  luci-lib-taskd-1.0.25.apk \
  luci-lib-xterm-4.18.0.apk \
  taskd-1.0.3-r2.apk | sort)"

if [[ "$actual_files" != "$expected_files" ]]; then
  echo "Unexpected staged files"
  diff -u <(printf '%s\n' "$expected_files") <(printf '%s\n' "$actual_files")
  exit 1
fi

cp "$work_dir/store.run" "$work_dir/tampered.run"
printf 'tampered\n' >> "$work_dir/tampered.run"
if "$repo_root/shell/prepare-store-packages.sh" \
  "$work_dir/tampered.run" \
  "$work_dir/tampered-target" \
  "$expected_sha" \
  "$header_size" \
  "$payload_size"; then
  echo "Tampered store bundle was accepted"
  exit 1
fi

echo "Store package staging test passed"
