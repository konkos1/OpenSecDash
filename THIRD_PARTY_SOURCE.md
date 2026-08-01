# Third-party source availability

OpenSecDash publishes source information with every tagged release so the
binary container can be traced back to the corresponding source versions.

## OpenSecDash

The source for the OpenSecDash release is the Git tag matching the container
version in this repository. The project is licensed under GNU AGPL-3.0; the
complete license is in `LICENSE`.

## Python application dependencies

`THIRD_PARTY_NOTICES.md` lists the exact source-distribution URL recorded in
`backend/uv.lock` for every Python package installed in the production image.
Components whose license requires covered source availability are also copied
into the release's `opensecdash-copyleft-sources.tar.gz` archive.

## Python and Debian container base

Every release contains these durable evidence files:

- `container-os-packages.json`: installed binary packages, their exact Debian
  source-package versions, copyright-file locations, and Debian Snapshot URLs;
- `opensecdash.spdx.json`: SPDX inventory generated from the final image;
- `opensecdash-copyleft-sources.tar.gz`: source archives for Debian packages
  whose installed copyright data identifies a GPL, LGPL, AGPL, MPL, or EPL
  license, plus covered Python application sources.

The release workflow builds these files from the same verified image that it
publishes. Exact Debian source files are fetched by content hash from Debian
Snapshot rather than from the moving package mirrors, and the downloaded hashes
are verified before the archive is created. Debian copyright and license files
also remain inside the image at `/usr/share/doc/<package>/copyright`.

If a corresponding source archive is unexpectedly unavailable, treat that as
a release defect and report it through the project's issue tracker.
