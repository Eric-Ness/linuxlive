// Parses every shell script with `bash -n`: the repository's scripts, the
// scripts shipped into images, and iso.env from every freshly compiled example.
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { ROOT, bash, fail, python, run } from './lib.mjs';

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const isShell = (p) => p.endsWith('.sh') || /^#!.*\b(ba)?sh\b/.test(readFileSync(p, 'utf8').split('\n', 1)[0]);
const files = [
  ...walk(join(ROOT, 'scripts')).filter(isShell),
  ...walk(join(ROOT, 'linuxlive', 'data', 'rootfs')).filter(isShell),
];

const scratch = mkdtempSync(join(tmpdir(), 'linuxlive-shell-'));
try {
  const specs = readdirSync(join(ROOT, 'examples')).filter((f) => f.endsWith('.yaml')).map((f) => `examples/${f}`);
  if (specs.length < 2) fail(`found only ${specs.length} example specs; the scan is broken`);
  for (const spec of specs) {
    const out = join(scratch, spec.replace(/\W+/g, '-'));
    const res = run(python(), ['-m', 'linuxlive', 'compile', spec, '-o', out]);
    if (res.code !== 0) fail(`compiling ${spec} failed:\n${res.out}`);
    files.push(join(out, 'iso.env'), join(out, 'rootfs', 'usr', 'lib', 'linuxlive', 'branding.env'));
  }
  if (files.length < 8) fail(`found only ${files.length} shell files; the scan is broken`);
  const sh = bash();
  for (const f of files) {
    const res = run(sh, ['-n', f]);
    if (res.code !== 0) fail(`${relative(ROOT, f)} does not parse:\n${res.out}`);
    console.log(`ok ${relative(ROOT, f) || f}`);
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
console.log(`SHELL SYNTAX OK (${files.length} files)`);
