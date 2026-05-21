#!/usr/bin/env node

const { spawn } = require("child_process");
const path = require("path");

const serverPath = path.join(__dirname, "bambu_mcp.py");
const python = process.env.BAMBU_AGENT_PYTHON || "/usr/bin/python3";

const child = spawn(python, [serverPath], {
  stdio: ["pipe", "pipe", "pipe"],
  env: {
    ...process.env,
    PYTHONUNBUFFERED: "1",
  },
});

process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);

const shutdown = () => {
  if (!child.killed) child.kill("SIGTERM");
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
