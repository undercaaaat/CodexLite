#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
source_root="${project_root}/.tools/codex-no-tools-linux"
source_binary="${source_root}/codex-exec"
source_build_info="${source_root}/build-info.json"
source_license="${source_root}/source/LICENSE"
source_notice="${source_root}/source/NOTICE"
build_root="${project_root}/.build"
asset_name="CodexLite-0.5.1-codex-0.144.6-linux-x86_64-ubuntu22.04"
bundle_root="${build_root}/${asset_name}"
archive_path="${build_root}/${asset_name}.tar.gz"
wheel_directory="${build_root}/wheels-linux"
wheel_path="${wheel_directory}/codex_batch-0.5.1-py3-none-any.whl"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "package-linux.sh requires Linux x86_64" >&2
    exit 1
fi

for required_path in \
    "${source_binary}" \
    "${source_build_info}" \
    "${source_license}" \
    "${source_notice}"; do
    if [[ ! -e "${required_path}" ]]; then
        echo "required build input is missing: ${required_path}" >&2
        exit 1
    fi
done

if ! command -v python3.11 >/dev/null 2>&1; then
    echo "python3.11 is required to package CodexLite" >&2
    exit 1
fi

if [[ -e "${bundle_root}" || -e "${archive_path}" ]]; then
    echo "release bundle already exists under ${build_root}" >&2
    exit 1
fi

mkdir -p \
    "${bundle_root}/bin" \
    "${bundle_root}/licenses" \
    "${bundle_root}/packages" \
    "${wheel_directory}"

python3.11 -m pip wheel "${project_root}" \
    --no-deps \
    --wheel-dir "${wheel_directory}"
if [[ ! -f "${wheel_path}" ]]; then
    echo "expected wheel was not produced: ${wheel_path}" >&2
    exit 1
fi

install -m 755 "${source_binary}" "${bundle_root}/bin/codex-exec"
install -m 644 "${source_build_info}" "${bundle_root}/build-info.json"
install -m 644 "${wheel_path}" \
    "${bundle_root}/packages/codex_batch-0.5.1-py3-none-any.whl"
install -m 644 "${project_root}/README-QUICKSTART-LINUX.md" \
    "${bundle_root}/README-QUICKSTART.md"
install -m 644 "${project_root}/MODIFICATIONS.md" \
    "${bundle_root}/MODIFICATIONS.md"
install -m 644 "${project_root}/LICENSE" \
    "${bundle_root}/LICENSE-CODEXLITE.txt"
install -m 644 "${source_license}" \
    "${bundle_root}/licenses/LICENSE-CODEX-APACHE-2.0.txt"
install -m 644 "${source_notice}" \
    "${bundle_root}/licenses/NOTICE-CODEX.txt"
install -m 644 \
    "${script_dir}/codex-rust-v0.144.6-no-tools.patch" \
    "${bundle_root}/codex-rust-v0.144.6-no-tools.patch"

tar -C "${build_root}" -czf "${archive_path}" "${asset_name}"
ls -lh "${archive_path}"
