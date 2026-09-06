#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function resolvePiPackageRoot() {
  const override = process.env.PI_CODING_AGENT_ROOT;
  if (override) return realpathSync(override);

  const found = spawnSync("sh", ["-lc", "command -v pi"], { encoding: "utf8" });
  if (found.status !== 0 || !found.stdout.trim()) fail("pi executable not found in PATH");
  const cli = realpathSync(found.stdout.trim());
  // Installed CLI is <package>/dist/bundle/cli.js.
  return dirname(dirname(dirname(cli)));
}

const sessionPath = process.argv[2];
if (!sessionPath) fail("usage: pi_context.mjs <session-jsonl>");

try {
  const packageRoot = resolvePiPackageRoot();
  const modulePath = resolve(packageRoot, "dist/index.js");
  const { SessionManager, ModelRuntime } = await import(pathToFileURL(modulePath).href);
  const manager = SessionManager.open(sessionPath);
  const branch = manager.getBranch();

  let lastAssistantIndex = -1;
  let lastAssistantEntry = null;
  for (let i = 0; i < branch.length; i += 1) {
    const entry = branch[i];
    if (
      entry.type === "message" &&
      entry.message?.role === "assistant" &&
      Number.isFinite(entry.message?.usage?.totalTokens)
    ) {
      lastAssistantIndex = i;
      lastAssistantEntry = entry;
    }
  }
  if (!lastAssistantEntry) fail("active Pi branch has no completed assistant usage");

  const laterModelChange = branch
    .slice(lastAssistantIndex + 1)
    .findLast((entry) => entry.type === "model_change");
  const assistant = lastAssistantEntry.message;
  const provider = laterModelChange?.provider ?? assistant.provider;
  const modelId = laterModelChange?.modelId ?? assistant.model;
  if (!provider || !modelId) fail("cannot determine Pi provider/model for active branch");

  const runtime = await ModelRuntime.create({ refreshOnCreate: false });
  const model = runtime.getModel(provider, modelId);
  const window = Number.isFinite(model?.contextWindow) ? model.contextWindow : null;

  process.stdout.write(
    `${JSON.stringify({
      total: assistant.usage.totalTokens,
      window,
      freshness: laterModelChange ? "stale-model-change" : "completed-call",
      observed_at: lastAssistantEntry.timestamp,
      provider,
      model: modelId,
      pi_package_version: packageRoot,
    })}\n`,
  );
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
