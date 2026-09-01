#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
resolved="$(
  cd "$repo_root"
  PROFILE=glinet_gl-mt3600be CUSTOM_PACKAGES='' bash -c '
    source shell/apk-custom-packages.sh
    printf "%s\n" "$CUSTOM_PACKAGES"
  '
)"

has_package() {
  [[ " $resolved " == *" $1 "* ]]
}

for package in dae daed luci-app-daede; do
  if ! has_package "$package"; then
    echo "Missing required daede package: $package"
    exit 1
  fi
done

for legacy_package in daed-geoip daed-geosite luci-app-daed luci-i18n-daed-zh-cn; do
  if has_package "$legacy_package"; then
    echo "Legacy daed package still selected: $legacy_package"
    exit 1
  fi
done

echo "Unified dae and daed package selection test passed"
