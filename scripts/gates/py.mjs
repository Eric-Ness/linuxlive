// Runs the repository's Python with the given arguments and passes its exit code through.
//   node scripts/gates/py.mjs scripts/check_catalog.py
import { spawnSync } from 'node:child_process';
import { ROOT, python } from './lib.mjs';

const res = spawnSync(python(), process.argv.slice(2), { cwd: ROOT, stdio: 'inherit' });
if (res.error) {
  console.error(`could not start Python: ${res.error.message}`);
  process.exit(2);
}
process.exit(res.status ?? 1);
