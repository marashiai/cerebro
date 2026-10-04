"""Select a task checkout without resetting branches or moving existing work."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from child_store_lib import store_upsert


def git(repo, *args, env=None):
    result = subprocess.run(['git', '-C', str(repo), *args], text=True, capture_output=True, env=env)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'git ' + ' '.join(args) + ' failed')
    return result.stdout.strip()


def common_dir(repo):
    return str((Path(repo) / git(repo, 'rev-parse', '--git-common-dir')).resolve())


def branch_at(repo):
    return git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')


def validate(saved):
    if not saved:
        raise ValueError('recorded workspace selection is missing')
    path = Path(saved['path'])
    if (not path.is_dir() or common_dir(path) != saved['common_dir']
            or str(Path(git(path, 'rev-parse', '--show-toplevel')).resolve()) != str(path)):
        raise ValueError('recorded checkout is missing or belongs to another repository; work was not recreated')


def prepare(repo, directory, branch, base, store, key, resume):
    repo = str(Path(git(repo, 'rev-parse', '--show-toplevel')).resolve())
    common = common_dir(repo)
    records = json.loads(Path(store).read_text()) if Path(store).exists() else {}
    saved = records.get(key, {}).get('workspace')
    selection = {'source_repo': repo, 'isolated': bool(directory),
                 'requested_branch': branch, 'requested_base': base}
    if resume or directory and saved:
        if not saved or any(saved.get(name) != value for name, value in selection.items()):
            raise ValueError('stored workspace selection is missing or differs; retain the original task options')
        validate(saved)
        if saved['common_dir'] != common:
            raise ValueError('recorded workspace belongs to another repository')
        saved['branch'] = branch_at(saved['path'])
        if not resume:
            saved['start_head'] = git(saved['path'], 'rev-parse', 'HEAD')
        store_upsert(store, key, {'workspace': saved})
        return saved

    if branch:
        git(repo, 'check-ref-format', 'refs/heads/' + branch)
    local_refs = git(repo, 'for-each-ref', '--format=%(refname)', 'refs/heads', 'refs/remotes/origin').splitlines()
    existing = bool(branch and 'refs/heads/' + branch in local_refs)
    tracking = bool(branch and not existing and 'refs/remotes/origin/' + branch in local_refs)
    # An existing branch keeps its own history; --base only seeds new work.
    source = ('refs/heads/' + branch if existing else 'refs/remotes/origin/' + branch if tracking
              else base if base and (branch or directory) else 'HEAD')
    start_head = git(repo, 'rev-parse', '--verify', source + '^{commit}')
    created_branch = bool(branch and not existing and not tracking)
    path = Path(directory).resolve() if directory else Path(repo)
    if directory:
        if path.exists():
            raise ValueError('workspace path already exists without matching task metadata: ' + str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            git(repo, 'worktree', 'add', str(path), branch)
        elif tracking:
            git(repo, 'worktree', 'add', '--track', '-b', branch, str(path), source)
        elif branch:
            git(repo, 'worktree', 'add', '-b', branch, str(path), source)
        else:
            git(repo, 'worktree', 'add', '--detach', str(path), start_head)
    elif branch and branch_at(repo) != branch:
        if existing:
            git(repo, 'switch', branch)
        elif tracking:
            git(repo, 'switch', '--track', '-c', branch, source)
        else:
            git(repo, 'switch', '-c', branch, start_head)
    workspace = {**selection, 'path': str(path), 'common_dir': common, 'branch': branch_at(path),
                 'start_head': start_head, 'created_worktree': bool(directory),
                 'created_branch': created_branch}
    store_upsert(store, key, {'workspace': workspace})
    return workspace


def snapshot(path):
    """Write the whole checkout, untracked files included, as a tree object.

    A private copy of the index keeps git's stat cache without touching the
    real index, refs or files.
    """
    with tempfile.TemporaryDirectory() as temporary:
        index = Path(temporary) / 'index'
        current = Path(path) / git(path, 'rev-parse', '--git-path', 'index')
        if current.is_file():
            shutil.copyfile(current, index)
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        git(path, 'add', '-A', env=env)
        return git(path, 'write-tree', env=env)


def refresh(store, key):
    saved = json.loads(Path(store).read_text())[key]['workspace']
    validate(saved)
    path = Path(saved['path'])
    saved['branch'] = branch_at(path)
    store_upsert(store, key, {'workspace': saved, 'repo': str(path), 'branch': saved['branch']})
    return saved


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'prepare':
            print(json.dumps(prepare(*sys.argv[2:8], sys.argv[8] == '1')))
        elif sys.argv[1] == 'refresh':
            print(json.dumps(refresh(*sys.argv[2:])))
        else:
            raise ValueError('unknown workspace operation')
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('cerebro: workspace: ' + str(error))
