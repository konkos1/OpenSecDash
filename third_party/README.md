# Third-party licensing

OpenSecDash keeps licensing evidence tied to the artifacts it distributes.

- `app-components.toml` records browser libraries that are copied directly
  into the application and declares the SVG assets created for OpenSecDash as
  first-party content.
- `licenses/` contains upstream texts that are not already preserved by an
  installed Python wheel.
- `scripts/generate_third_party_notices.py` derives the application notice
  from the locked runtime environment and this manifest.
- `website/scripts/generate-third-party-notices.mjs` derives the website
  notice from `package-lock.json` and the installed npm packages.

Generated notices are committed so a source checkout, the application image,
and the deployed website all expose the same information. CI regenerates them
and fails when the committed copies are stale or a dependency has no declared
license.
