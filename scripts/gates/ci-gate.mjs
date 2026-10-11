// Verifies a CI result for the exact commit checked out here.
//
//   node scripts/gates/ci-gate.mjs unit
//   node scripts/gates/ci-gate.mjs <example> <iso|boot|session>
//
// <example> names a spec in examples/, such as testos-ubuntu. The script finds
// the latest finished (not cancelled) `spike` workflow run for HEAD, downloads
// the run's artifact, and re-checks it locally: the unit summary against a
// local test count, or the build results with `linuxlive verify-results`. CI
// saying "success" is not enough on its own.
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ROOT, fail, python, run } from './lib.mjs';

const [target, check] = process.argv.slice(2);
const EXAMPLES = readdirSync(join(ROOT, 'examples')).filter((f) => f.endsWith('.yaml')).map((f) => f.slice(0, -5)).sort();
if (!(target === 'unit' || (EXAMPLES.includes(target) && ['iso', 'boot', 'session'].includes(check)))) {
  console.error(`usage: ci-gate.mjs unit | ci-gate.mjs <${EXAMPLES.join('|')}> <iso|boot|session>`);
  process.exit(2);
}

function git(...args) {
  const res = run('git', args);
  if (res.code !== 0) fail(`git ${args.join(' ')} failed:\n${res.out}`);
  return res.stdout.trim();
}
function gh(...args) {
  const res = run('gh', args);
  if (res.code !== 0) fail(`gh ${args.join(' ')} failed:\n${res.out}`);
  return res.stdout;
}

// The ledger is updated as gates pass, and neither it nor the README or the
// working notes affect what gets built or tested, so commits touching only
// those do not run CI (see the workflow's paths-ignore). Verify the newest
// commit that changed anything else; by construction nothing after it but
// those files differs.
const DOCS = ['GATES.md', 'README.md', 'NOTES.md'];
const dirty = git('status', '--porcelain').split('\n').filter((l) => l && !DOCS.some((d) => l.endsWith(d)));
if (dirty.length) fail(`working tree has changes CI has not seen:\n${dirty.join('\n')}`);
const sha = git('log', '-1', '--format=%H', '--', '.', ...DOCS.map((d) => `:(exclude)${d}`));
if (!sha) fail('no commit found outside the docs');
console.log(`verifying ${sha.slice(0, 12)} (HEAD ${git('rev-parse', '--short=12', 'HEAD')})`);
const remote = git('remote', 'get-url', 'origin');
const repo = remote.match(/github\.com[:/](.+?)(?:\.git)?$/)?.[1];
if (!repo) fail(`origin ${remote} is not a GitHub repository`);
gh('api', `repos/${repo}/commits/${sha}`, '--jq', '.sha'); // fails unless HEAD is pushed

const runs = JSON.parse(gh('run', 'list', '-R', repo, '--workflow', 'spike.yml', '--commit', sha,
  '--json', 'databaseId,status,conclusion,createdAt', '--limit', '20'));
// A run started by hand (to keep the ISOs) cancels the push run for the same
// commit, so a cancelled run proves nothing either way.
const done = runs
  .filter((r) => r.status === 'completed' && r.conclusion !== 'cancelled')
  .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
if (!done.length) fail(`no finished spike run for ${sha.slice(0, 12)} yet (${runs.length} queued, running or cancelled)`);
const runId = String(done[0].databaseId);
const jobs = JSON.parse(gh('run', 'view', runId, '-R', repo, '--json', 'jobs')).jobs;

const scratch = mkdtempSync(join(tmpdir(), 'linuxlive-ci-'));
try {
  if (target === 'unit') {
    const job = jobs.find((j) => j.name === 'unit');
    if (!job || job.conclusion !== 'success') fail(`unit job in run ${runId} concluded ${job?.conclusion ?? 'missing'}`);
    gh('run', 'download', runId, '-R', repo, '-n', 'unit-summary', '-D', scratch);
    const summary = JSON.parse(readFileSync(join(scratch, 'unit-summary.json'), 'utf8'));
    if (summary.commit !== sha) fail(`unit summary is for ${summary.commit}, not ${sha}`);
    // Count the tests here, independently, and require CI to have passed all of them.
    const collected = run(python(), ['-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider']);
    const local = Number.parseInt(collected.out.match(/(\d+) tests? collected/)?.[1] ?? '0', 10);
    if (local === 0) fail(`could not count tests locally:\n${collected.out}`);
    if (summary.passed !== local || summary.failed !== 0) {
      fail(`CI passed ${summary.passed} and failed ${summary.failed}; ${local} tests exist locally`);
    }
    if (summary.shellcheck !== 'ok') fail(`shellcheck: ${summary.shellcheck}`);
    const compiled = [...(summary.compiled ?? [])].sort();
    if (JSON.stringify(compiled) !== JSON.stringify(EXAMPLES)) {
      fail(`CI compiled ${JSON.stringify(compiled)}; the examples are ${JSON.stringify(EXAMPLES)}`);
    }
    console.log(`run ${runId}: ${summary.passed} tests passed, shellcheck ok, ${summary.compiled.join(', ')} compiled`);
    console.log('CI GATE OK unit');
  } else {
    const job = jobs.find((j) => j.name === `build (${target})`);
    if (!job || job.status !== 'completed') fail(`build (${target}) job in run ${runId} is ${job?.status ?? 'missing'}`);
    const dl = run('gh', ['run', 'download', runId, '-R', repo, '-n', `results-${target}`, '-D', scratch]);
    if (dl.code !== 0) fail(`run ${runId} has no results-${target} artifact:\n${dl.out}`);
    const res = run(python(), ['-m', 'linuxlive', 'verify-results', '--spec', `examples/${target}.yaml`,
      '--results', scratch, '--check', check, '--expect-commit', sha]);
    process.stdout.write(res.out);
    if (res.code !== 0 || !res.out.includes(`VERIFY OK ${check}`)) fail(`${target} ${check} checks failed in run ${runId}`);
    console.log(`CI GATE OK ${target} ${check}`);
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
