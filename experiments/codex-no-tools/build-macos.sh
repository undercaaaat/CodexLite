#!/usr/bin/env bash

set -euo pipefail

tag="rust-v0.144.6"
commit="5d1fbf26c43abc65a203928b2e31561cb039e06d"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
patch_path="${script_dir}/codex-rust-v0.144.6-no-tools.patch"
destination="${project_root}/.tools/codex-no-tools-macos"
source_path="${destination}/source"
binary_path="${destination}/codex-exec"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "build-macos.sh must run on macOS" >&2
    exit 1
fi

case "$(uname -m)" in
    arm64)
        architecture="arm64"
        rust_target="aarch64-apple-darwin"
        ;;
    x86_64)
        architecture="x86_64"
        rust_target="x86_64-apple-darwin"
        ;;
    *)
        echo "unsupported macOS architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

for command_name in git cargo rustc python3 xcode-select; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "${command_name} is required" >&2
        exit 1
    fi
done

if ! xcode-select -p >/dev/null 2>&1; then
    echo "install the Xcode Command Line Tools first" >&2
    exit 1
fi

mkdir -p "${destination}"
if [[ ! -d "${source_path}/.git" ]]; then
    git clone --depth 1 --branch "${tag}" \
        https://github.com/openai/codex.git "${source_path}"
fi

pushd "${source_path}" >/dev/null
actual_commit="$(git rev-parse HEAD)"
if [[ "${actual_commit}" != "${commit}" ]]; then
    echo "unexpected source commit: ${actual_commit}" >&2
    exit 1
fi

if git apply --reverse --check "${patch_path}" >/dev/null 2>&1; then
    :
elif git apply --check "${patch_path}"; then
    git apply "${patch_path}"
else
    echo "source tree is neither clean nor already patched" >&2
    exit 1
fi

git diff --check

pushd codex-rs >/dev/null
export CARGO_TARGET_DIR="${source_path}/codex-rs/target"
export CARGO_NET_GIT_FETCH_WITH_CLI=true

# codex-exec does not use the realtime workspace member or its large WebRTC checkout.
if ! grep -Fqx '    "realtime-webrtc",' Cargo.toml; then
    echo "expected realtime-webrtc workspace member was not found" >&2
    exit 1
fi
sed -i.bak '/^    "realtime-webrtc",$/d' Cargo.toml
rm Cargo.toml.bak

cargo test -p codex-features
cargo build --release -p codex-exec --bin codex-exec
cargo_version="$(cargo --version)"
rustc_version="$(rustc --version)"
popd >/dev/null

cp "${source_path}/codex-rs/target/release/codex-exec" "${binary_path}"
chmod 755 "${binary_path}"

{
    printf '{\n'
    printf '  "architecture": "%s",\n' "${architecture}"
    printf '  "cargo": "%s",\n' "${cargo_version}"
    printf '  "codex_commit": "%s",\n' "${commit}"
    printf '  "codex_tag": "%s",\n' "${tag}"
    printf '  "mode": "answer_only",\n'
    printf '  "platform": "macos",\n'
    printf '  "rust_target": "%s",\n' "${rust_target}"
    printf '  "rustc": "%s"\n' "${rustc_version}"
    printf '}\n'
} >"${destination}/build-info.json"

popd >/dev/null

python3 "${script_dir}/verify_request.py" \
    --codex-command "${binary_path}" \
    --model "gpt-5.6-sol"

"${binary_path}" --version
printf 'Built patched macOS Codex exec: %s\n' "${binary_path}"
