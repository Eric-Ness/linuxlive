// Shared helpers for the gate scripts in this directory. They run on Windows
// (cmd.exe) and Linux, so they avoid shell features and resolve tools here.
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');

// The repository's virtualenv Python, falling back to python3/python on PATH.
export function python() {
  const candidates = process.platform === 'win32'
    ? [join(ROOT, '.venv', 'Scripts', 'python.exe')]
    : [join(ROOT, '.venv', 'bin', 'python')];
  for (const c of candidates) if (existsSync(c)) return c;
  return process.platform === 'win32' ? 'python' : 'python3';
}

// Git for Windows' bash, not the WSL launcher that may also be on PATH.
export function bash() {
  if (process.platform !== 'win32') return 'bash';
  for (const c of ['C:/Program Files/Git/bin/bash.exe', 'C:/Program Files (x86)/Git/bin/bash.exe']) {
    if (existsSync(c)) return c;
  }
  return 'bash';
}

export function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, ...opts });
  if (res.error) throw res.error;
  return { code: res.status, out: `${res.stdout ?? ''}${res.stderr ?? ''}`, stdout: res.stdout ?? '' };
}

export function fail(message) {
  console.error(`GATE FAILED: ${message}`);
  process.exit(1);
}
