#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="$repo_root/.github/workflows/build-wireless-router25.12.yml"

grep -Fq 'tag_name: ${{ github.event.inputs.profile }}-run-${{ github.run_number }}' "$workflow"
grep -Fq 'name: "ImmortalWrt 25.12.x | ${{ github.event.inputs.profile }} | Run #${{ github.run_number }}"' "$workflow"
grep -Fq 'target_commitish: ${{ github.sha }}' "$workflow"

echo "Unique release configuration test passed"
