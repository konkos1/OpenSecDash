#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" != "3" ]]; then
  echo "usage: download-container-sources.sh REPORT_JSON APP_NOTICES_JSON OUTPUT_DIRECTORY" >&2
  exit 2
fi

report_json="$1"
app_notices_json="$2"
output_directory="$3"

if [[ -e "${output_directory}" ]]; then
  echo "source output already exists: ${output_directory}" >&2
  exit 1
fi

mkdir -p "${output_directory}/debian" "${output_directory}/python"
output_absolute="$(cd "${output_directory}" && pwd)"

jq -r '
  [.packages[] | select(.source_required) | [.source_package, .source_version, .source_api_url]]
  | unique
  | .[]
  | @tsv
' "${report_json}" > "${output_directory}/debian/source-packages.tsv"

touch "${output_directory}/debian/source-files.tsv"
while IFS=$'\t' read -r package version source_api_url; do
  [[ -n "${package}" ]] || continue
  source_response="$(curl --fail --location --retry 3 --silent --show-error "${source_api_url}")"
  if ! jq -e '.result | type == "array" and length > 0' \
    >/dev/null <<< "${source_response}"; then
    echo "Debian Snapshot returned no source files for ${package} ${version}" >&2
    exit 1
  fi

  while IFS= read -r source_hash; do
    metadata_url="https://snapshot.debian.org/mr/file/${source_hash}/info"
    metadata="$(curl --fail --location --retry 3 --silent --show-error "${metadata_url}")"
    file_name="$(jq -r '.result[0].name // empty' <<< "${metadata}")"
    if [[ -z "${file_name}" || "${file_name}" == */* ]]; then
      echo "Invalid Debian Snapshot filename for ${source_hash}: ${file_name}" >&2
      exit 1
    fi
    target="${output_absolute}/debian/${file_name}"
    curl --fail --location --retry 3 --silent --show-error \
      --output "${target}" "https://snapshot.debian.org/file/${source_hash}"
    actual_hash="$(sha1sum "${target}")"
    actual_hash="${actual_hash%% *}"
    if [[ "${actual_hash}" != "${source_hash}" ]]; then
      echo "Hash mismatch for Debian source file ${file_name}" >&2
      exit 1
    fi
    printf '%s\t%s\t%s\t%s\n' \
      "${package}" "${version}" "${source_hash}" "${file_name}" \
      >> "${output_directory}/debian/source-files.tsv"
  done < <(jq -r '.result[].hash' <<< "${source_response}")
done < "${output_directory}/debian/source-packages.tsv"

jq -r '
  .components[]
  | select(.source_required)
  | [.name, .version, .source_archive]
  | @tsv
' "${app_notices_json}" > "${output_directory}/python/source-packages.tsv"

while IFS=$'\t' read -r package version source_url; do
  [[ -n "${package}" ]] || continue
  archive_name="${source_url##*/}"
  safe_name="${package//[^A-Za-z0-9._-]/-}-${version}-${archive_name}"
  safe_name="${safe_name//[^A-Za-z0-9._-]/-}"
  curl --fail --location --retry 3 --output "${output_directory}/python/${safe_name}" "${source_url}"
done < "${output_directory}/python/source-packages.tsv"

cp "${report_json}" "${output_directory}/container-os-packages.json"
cp "${app_notices_json}" "${output_directory}/application-components.json"
