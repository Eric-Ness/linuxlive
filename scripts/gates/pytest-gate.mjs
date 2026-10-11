// Runs pytest on the given files and prints MARKER only when every test passed
// and at least MIN_TESTS ran.
//   node scripts/gates/pytest-gate.mjs "SPEC GATE OK" 14 tests/test_spec.py
import { fail, python, run } from './lib.mjs';

const [marker, minText, ...files] = process.argv.slice(2);
const min = Number.parseInt(minText, 10);
if (!marker || !Number.isInteger(min) || files.length === 0) {
  console.error('usage: pytest-gate.mjs MARKER MIN_TESTS FILE...');
  process.exit(2);
}

const { code, out } = run(python(), ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', ...files]);
process.stdout.write(out);
const summary = out.trim().split('\n').pop() ?? '';
const count = (word) => Number.parseInt(summary.match(new RegExp(`(\\d+) ${word}`))?.[1] ?? '0', 10);
const passed = count('passed');
const bad = count('failed') + count('error') + count('errors');
if (code !== 0) fail(`pytest exited with ${code}`);
if (bad > 0) fail(`${bad} tests failed or errored`);
if (passed < min) fail(`only ${passed} tests passed; the gate needs at least ${min}`);
console.log(`${marker} (${passed} passed)`);
