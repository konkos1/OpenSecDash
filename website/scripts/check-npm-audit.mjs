import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const allowlist = JSON.parse(
  readFileSync(new URL("../audit-allowlist.json", import.meta.url), "utf8"),
);

function audit(args) {
  const result = spawnSync("npm", ["audit", "--package-lock-only", "--json", ...args], {
    cwd: new URL("..", import.meta.url),
    encoding: "utf8",
  });
  if (result.error || !result.stdout) {
    throw result.error ?? new Error(result.stderr || "npm audit produced no report");
  }
  const report = JSON.parse(result.stdout);
  if (report.auditReportVersion !== 2 || typeof report.vulnerabilities !== "object") {
    throw new Error(report.message || result.stderr || "npm audit returned an invalid report");
  }
  return report;
}

function blockingAdvisories(report) {
  const advisories = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
    for (const finding of vulnerability.via ?? []) {
      if (
        typeof finding === "object"
        && ["high", "critical"].includes(finding.severity)
      ) {
        const id = new URL(finding.url).pathname.split("/").pop();
        advisories.set(id, finding);
      }
    }
  }
  return advisories;
}

const runtimeFindings = blockingAdvisories(audit(["--omit=dev"]));
if (runtimeFindings.size > 0) {
  throw new Error(`Runtime npm audit failed: ${[...runtimeFindings.keys()].join(", ")}`);
}
console.log("Runtime npm audit: no high or critical findings.");

const allowed = new Map(allowlist.advisories.map((entry) => [entry.id, entry]));
const today = new Date().toISOString().slice(0, 10);
const devFindings = blockingAdvisories(audit([]));
const unexpected = [];
for (const [id, finding] of devFindings) {
  const exception = allowed.get(id);
  if (!exception) {
    unexpected.push(`${id} (${finding.title})`);
  } else if (exception.expires < today) {
    unexpected.push(`${id} (exception expired ${exception.expires})`);
  } else {
    console.log(`Allowed until ${exception.expires}: ${id} - ${exception.reason}`);
  }
}

if (unexpected.length > 0) {
  throw new Error(`Website build dependency audit failed: ${unexpected.join("; ")}`);
}
console.log(`Website build audit: ${devFindings.size} documented high/critical finding(s), no unexpected findings.`);
