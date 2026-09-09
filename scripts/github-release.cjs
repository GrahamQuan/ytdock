// Called only for trusted main-branch workflows. Never replace a published release.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function version(value) {
  if (!/^\d+\.\d+\.\d+$/.test(value)) throw new Error('Invalid stable version');
  return value.split('.').map(BigInt);
}
function compare(a, b) {
  const left = version(a), right = version(b);
  for (let i = 0; i < 3; i++) {
    if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
  }
  return 0;
}
async function inspect({ github, context }, current) {
  version(current);
  const releases = await github.paginate(github.rest.repos.listReleases, { ...context.repo, per_page: 100 });
  const existing = releases.find(r => r.tag_name === `v${current}`);
  if (existing && !existing.draft) return { skip: true, existing };
  for (const release of releases) {
    if (!release.draft && !release.prerelease && /^v\d+\.\d+\.\d+$/.test(release.tag_name)
        && compare(current, release.tag_name.slice(1)) <= 0) {
      throw new Error('New stable version must be higher than published stable releases');
    }
  }
  return { skip: false, existing };
}
async function plan(args) {
  const state = await inspect(args, process.env.RELEASE_VERSION);
  args.core.setOutput('publish', !state.skip);
  args.core.info(state.skip ? 'Version already published; skipping without changes.' : 'New version: build and validate before publishing.');
}
function assets(directory, current) {
  version(current);
  const base = `ytdock-v${current}-macos-arm64`;
  const result = [];
  for (const extension of ['zip', 'tar.gz']) {
    const name = `${base}.${extension}`;
    const data = fs.readFileSync(path.join(directory, name));
    const checksum = fs.readFileSync(path.join(directory, name + '.sha256'));
    const digest = crypto.createHash('sha256').update(data).digest('hex');
    if (!data.length || checksum.toString() !== `${digest}  ${name}\n`) {
      throw new Error(`Missing/invalid archive or SHA256: ${name}`);
    }
    result.push({ name, data }, { name: name + '.sha256', data: checksum });
  }
  return result;
}
async function tagCommit(github, repo, tag) {
  try {
    let object = (await github.rest.git.getRef({ ...repo, ref: `tags/${tag}` })).data.object;
    for (let i = 0; object.type === 'tag' && i < 8; i++) {
      object = (await github.rest.git.getTag({ ...repo, tag_sha: object.sha })).data.object;
    }
    if (object.type !== 'commit') throw new Error('Tag does not resolve to a commit');
    return object.sha;
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
}
async function publish(args) {
  const { github, context, core } = args;
  const current = process.env.RELEASE_VERSION;
  const state = await inspect(args, current);
  if (state.skip) { core.info('Already published; nothing changed.'); return; }
  const files = assets('dist', current); // Verify everything before remote mutations.
  const tag = `v${current}`;
  const commit = await tagCommit(github, context.repo, tag);
  if (commit && commit !== context.sha) throw new Error('Existing tag belongs to another commit; refusing to move it');
  if (state.existing && !commit && state.existing.target_commitish !== context.sha) {
    throw new Error('Draft belongs to another commit');
  }
  if (!commit) await github.rest.git.createRef({ ...context.repo, ref: `refs/tags/${tag}`, sha: context.sha });
  let release = state.existing;
  if (!release) {
    release = (await github.rest.repos.createRelease({
      ...context.repo, tag_name: tag, target_commitish: context.sha,
      name: `YTDock ${tag}`, draft: true, prerelease: false,
      generate_release_notes: true,
      body: 'macOS 14+ / Apple Silicon. Python and QuickJS bundled; FFmpeg/ffprobe external.\n\nValidated by automated tests and isolated package smoke checks. Online YouTube downloading and real cross-version self-upgrade are not verified by this workflow. Ad-hoc signed, not Apple-notarized.\n\nInstall/update: `curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash`\n',
    })).data;
  }
  // A failed upload leaves a draft. A rerun at the same commit can safely retry it.
  const oldAssets = await github.paginate(github.rest.repos.listReleaseAssets, { ...context.repo, release_id: release.id, per_page: 100 });
  for (const file of files) {
    const old = oldAssets.find(a => a.name === file.name);
    if (old) await github.rest.repos.deleteReleaseAsset({ ...context.repo, asset_id: old.id });
    await github.rest.repos.uploadReleaseAsset({
      ...context.repo, release_id: release.id, name: file.name, data: file.data,
      headers: { 'content-type': 'application/octet-stream', 'content-length': file.data.length },
    });
  }
  await github.rest.repos.updateRelease({ ...context.repo, release_id: release.id, draft: false, prerelease: false, make_latest: 'true' });
  core.info(`Published ${tag}`);
}
module.exports = { compare, assets, plan, publish };
