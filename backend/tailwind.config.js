// Build config for the pre-generated app/static/css/tailwind.css.
//
// The UI used to load https://cdn.tailwindcss.com (the Play CDN), which
// JIT-compiles all CSS in the browser on every page load and re-scans the
// DOM on every htmx swap - visible as a flash of unstyled content on each
// navigation, constant CPU work during auto-refresh, and a hard external
// dependency for a self-hosted tool. The pre-built stylesheet removes all
// of that. Regenerate after adding new Tailwind utility classes:
//
//   npx -y tailwindcss@3.4.17 -c tailwind.config.js \
//     -i tailwind.input.css -o app/static/css/tailwind.css --minify
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/static/js/app.js",
    "./app/api/pages.py",
  ],
};
