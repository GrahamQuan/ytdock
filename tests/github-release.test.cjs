const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { compare, assets, plan, publish } = require('../scripts/github-release.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ytdock-action-'));
  const cwd = process.cwd();
  process.chdir(root);
  t.after(() => { process.chdir(cwd); fs.rmSync(root, { recursive: true, force: true }); });
  fs.mkdirSync('dist');
  for (const extension of ['zip', 'tar.gz']) {
    const name = `ytdock-v1.2.3-macos-arm64.${extension}`;
    const data = Buffer.from(extension);
    fs.writeFileSync(path.join('dist', name), data);
    fs.writeFileSync(path.join('dist', name + '.sha256'), crypto.createHash('sha256').update(data).digest('hex') + '  ' + name + '\n');
  }
}
function client(releases = [], commit = null, failUpload = false) {
  const calls = [];
  const repos = {
    listReleases: 'releases', listReleaseAssets: 'assets',
    createRelease: async p => { calls.push(['create', p]); return { data: { id: 7 } }; },
    deleteReleaseAsset: async p => calls.push(['delete', p]),
    uploadReleaseAsset: async p => { calls.push(['upload', p.name]); if (failUpload) throw Error('upload failed'); },
    updateRelease: async p => calls.push(['publish', p]),
  };
  const git = {
    getRef: async () => { if (!commit) throw Object.assign(Error('missing'), { status: 404 }); return { data: { object: { type: 'commit', sha: commit } } }; },
    createRef: async p => calls.push(['tag', p]),
  };
  return { calls, args: {
    github: { rest: { repos, git }, paginate: async method => method === 'releases' ? releases : [] },
    context: { repo: { owner: 'test', repo: 'ytdock' }, sha: 'tested-commit' },
    core: { info() {}, setOutput: (...p) => calls.push(['output', ...p]) },
  } };
}
process.env.RELEASE_VERSION = '1.2.3';
test('numeric stable version comparison rejects prereleases', () => {
  assert.equal(compare('1.10.0', '1.9.0'), 1);
  assert.equal(compare('1.2.3', '1.2.3'), 0);
  assert.throws(() => compare('1.2.3-rc1', '1.2.3'));
});
test('published version skips without remote mutation or build', async () => {
  const c = client([{ tag_name: 'v1.2.3', draft: false }]);
  await plan(c.args); await publish(c.args);
  assert.deepEqual(c.calls, [['output', 'publish', false]]);
});
test('older new version is rejected; prereleases do not block stable', async () => {
  await assert.rejects(plan(client([{ tag_name: 'v2.0.0', draft: false, prerelease: false }]).args));
  const c = client([{ tag_name: 'v2.0.0', draft: false, prerelease: true }]);
  await plan(c.args); assert.deepEqual(c.calls, [['output', 'publish', true]]);
});
test('checksum and all four files verified before tag creation', async t => {
  fixture(t); assert.equal(assets('dist', '1.2.3').length, 4);
  fs.writeFileSync('dist/ytdock-v1.2.3-macos-arm64.zip', 'corrupted');
  const c = client(); await assert.rejects(publish(c.args)); assert.equal(c.calls.length, 0);
});
test('existing tag at a different commit is never moved', async t => {
  fixture(t); const c = client([], 'other');
  await assert.rejects(publish(c.args), /another commit/); assert.equal(c.calls.length, 0);
});
test('publish only after tag, draft and four successful uploads', async t => {
  fixture(t); const c = client(); await publish(c.args);
  assert.deepEqual(c.calls.map(c => c[0]), ['tag', 'create', 'upload', 'upload', 'upload', 'upload', 'publish']);
  assert.equal(c.calls[0][1].sha, 'tested-commit');
  assert.equal(c.calls[1][1].draft, true);
  assert.equal(c.calls.at(-1)[1].draft, false);
});
test('failed upload leaves draft unpublished', async t => {
  fixture(t); const c = client([], null, true);
  await assert.rejects(publish(c.args), /upload failed/);
  assert(!c.calls.some(c => c[0] === 'publish'));
});
test('same-commit draft can be resumed without creating another release', async t => {
  fixture(t); const c = client([{ tag_name: 'v1.2.3', draft: true, id: 7 }], 'tested-commit');
  await publish(c.args);
  assert.deepEqual(c.calls.map(c => c[0]), ['upload', 'upload', 'upload', 'upload', 'publish']);
});
