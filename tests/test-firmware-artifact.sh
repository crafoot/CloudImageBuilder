#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="$repo_root/.github/workflows/build-wireless-router25.12.yml"

grep -Fq -- '- name: Upload firmware as workflow artifact' "$workflow"
grep -Fq 'uses: actions/upload-artifact@v4' "$workflow"
grep -Fq 'name: ${{ github.event.inputs.profile }}-firmware-run-${{ github.run_number }}' "$workflow"
grep -Fq 'path: ${{ github.workspace }}/bin/targets/${{ env.platform }}/*' "$workflow"
grep -Fq 'if-no-files-found: error' "$workflow"
grep -Fq 'retention-days: 30' "$workflow"

echo "Firmware artifact configuration test passed"
