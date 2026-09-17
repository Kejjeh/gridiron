"""golden_run.py — A/B output-equivalence verifier for behavior-preserving refactors.

Ported from plv_clone (where this workflow ran ~6x during a production
audit): snapshot prod outputs, run the pipelines on CURRENT code (phase A),
apply the refactor, re-run (phase B), and verify every output is identical —
then ALWAYS restore the prod outputs, which may carry enrichment the raw
pipeline run does not.

Data-coupled-golden lesson: a golden capture is only meaningful while its
INPUTS are frozen. Phase A hashes every input; phase B refuses to diff if
any hash drifted — a drifted input means the A/B diff measures the data
refresh, not your refactor.

TARGETS is empty on day one. Register each weekly pipeline (projections,
waiver board, start/sit) here as it lands; until then use --target custom:

  python scripts/ci/golden_run.py --target custom --phase A \
      --cmd "python -X utf8 scripts/xfp/weekly_projections.py" \
      --outputs data/outputs/weekly_projections.csv \
      --inputs data/research/cache/weekly_stats_2026.parquet
  ... apply behavior-preserving edits ...
  python scripts/ci/golden_run.py --target custom --phase B --cmd ... (same args)

Exit codes: 0 = all outputs identical; 1 = diffs found / command failed;
2 = refusal (lock held, manifest missing, input drift, bad args).
"""
import argparse
import functools
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows-safe console (cp1252 chokes on the check/warn glyphs)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

# flush every print so parent progress interleaves correctly with child stdout
print = functools.partial(print, flush=True)

REPO = Path(__file__).resolve().parents[2]
OK, WARN = '✓', '⚠'

# Register named pipelines as they land (see plv_clone for the shape:
# {'name': {'commands': [...], 'outputs': [...], 'inputs': [...]}}).
#
# weekly_report renders OFFLINE from the ingest cache, so an A/B run is
# reproducible as long as the cache is not re-pulled between phases. The
# manifest is listed as an input so a mid-run re-pull is caught as input
# drift rather than reported as a behavior change. The compared output is the
# stable `_latest` copy — the week-stamped file changes name every week, and
# the markdown carries a generated-at line that always differs.
TARGETS: dict = {
    'weekly_report': {
        'commands': [
            'python -X utf8 scripts/weekly/report.py --write --anonymous',
        ],
        'outputs': [
            'data/outputs/weekly_report_latest.csv',
        ],
        'inputs': [
            'data/research/cache/season2026/manifest.json',
        ],
    },
}


def scratch_root() -> Path:
    env = os.environ.get('GOLDEN_RUN_DIR')
    return Path(env) if env else REPO / 'data/research/.golden_run'


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run_commands(commands):
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    for argv in commands:
        print(f'\n>>> {subprocess.list2cmdline(argv)}')
        t0 = time.time()
        rc = subprocess.run(argv, cwd=str(REPO), env=env).returncode
        print(f'    ({time.time() - t0:.1f}s, exit {rc})')
        if rc != 0:
            raise RuntimeError(f'command failed (exit {rc}): {subprocess.list2cmdline(argv)}')


def rel_dest(base: Path, out_rel: str) -> Path:
    """Destination for an output copy, preserving repo-relative structure."""
    p = Path(out_rel)
    if p.is_absolute():
        try:
            p = p.relative_to(REPO)
        except ValueError:
            p = Path(p.name)
    return base / p


def copy_outputs(outputs, dest_base: Path, label: str):
    copied = []
    for out in outputs:
        src = REPO / out
        if not src.exists():
            print(f'{WARN} {label}: missing (not copied): {out}')
            continue
        dst = rel_dest(dest_base, out)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(out)
    return copied


def restore_prod(prod_dir: Path, outputs):
    restored = 0
    for out in outputs:
        src = rel_dest(prod_dir, out)
        if src.exists():
            dst = REPO / out
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
    print(f'{OK} restored {restored} prod output file(s) over the live copies '
          f'(prod outputs may carry enrichment — never leave raw pipeline output live).')


def diff_csv(a: Path, b: Path) -> str:
    import pandas as pd
    try:
        da, db = pd.read_csv(a), pd.read_csv(b)
    except Exception as e:
        return f'DIFFERENT (unreadable as CSV: {e})'
    try:
        pd.testing.assert_frame_equal(da, db)
        return 'EQUIVALENT (frames equal; byte diff is float formatting only)'
    except AssertionError as e:
        if list(da.columns) != list(db.columns):
            only_a = [c for c in da.columns if c not in db.columns]
            only_b = [c for c in db.columns if c not in da.columns]
            return f'DIFFERENT (columns: only-A={only_a} only-B={only_b})'
        if len(da) != len(db):
            return f'DIFFERENT (row count {len(da)} vs {len(db)})'
        bad = []
        for c in da.columns:
            try:
                pd.testing.assert_series_equal(da[c], db[c], check_names=False)
            except AssertionError:
                bad.append(c)
            if len(bad) >= 5:
                break
        detail = f'first differing columns: {bad}' if bad else str(e).splitlines()[0]
        return f'DIFFERENT ({detail})'


def diff_parquet(a: Path, b: Path) -> str:
    import pandas as pd
    try:
        da, db = pd.read_parquet(a), pd.read_parquet(b)
    except Exception as e:
        return f'DIFFERENT (unreadable as parquet: {e})'
    if da.equals(db):
        return 'EQUIVALENT (DataFrames equal; byte diff is encoding only)'
    bad = [c for c in da.columns if c not in db.columns or not da[c].equals(db[c])][:5]
    return f'DIFFERENT (first differing columns: {bad})'


def diff_output(a: Path, b: Path) -> str:
    """Return 'IDENTICAL' / 'EQUIVALENT ...' / 'DIFFERENT ...' / 'MISSING ...'."""
    if not a.exists():
        return 'MISSING (no phase-A copy)'
    if not b.exists():
        return 'MISSING (phase-B run produced no file)'
    if a.stat().st_size == b.stat().st_size and md5_file(a) == md5_file(b):
        return 'IDENTICAL'
    suf = a.suffix.lower()
    if suf == '.csv':
        return diff_csv(a, b)
    if suf == '.parquet':
        return diff_parquet(a, b)
    if suf == '.json':
        try:
            with open(a, encoding='utf-8') as fa, open(b, encoding='utf-8') as fb:
                if json.load(fa) == json.load(fb):
                    return 'EQUIVALENT (JSON payload equal; byte diff is formatting only)'
        except Exception:
            pass
        return 'DIFFERENT (json bytes + payload differ)'
    return 'DIFFERENT (bytes differ)'


def resolve_target(args):
    if args.target == 'custom':
        if not args.cmd or not args.outputs:
            print('ERROR: --target custom requires at least one --cmd and --outputs.')
            sys.exit(2)
        commands = [shlex.split(c) for c in args.cmd]
        spec = {'commands': commands, 'outputs': list(args.outputs),
                'inputs': list(args.inputs or [])}
        if not spec['inputs']:
            print(f'{WARN} custom target with no --inputs: phase B cannot detect '
                  f'data drift — the A/B diff is only trustworthy if you KNOW the '
                  f'inputs were frozen.')
        return spec
    spec = TARGETS[args.target]
    return {'commands': [list(c) for c in spec['commands']],
            'outputs': list(spec['outputs']), 'inputs': list(spec['inputs'])}


def phase_a(args, spec, tdir: Path):
    prod_dir, a_dir = tdir / 'prod', tdir / 'A'
    for d in (prod_dir, a_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    print(f'[A] snapshotting prod outputs -> {prod_dir}')
    prod_copied = copy_outputs(spec['outputs'], prod_dir, 'prod')

    print('[A] hashing inputs (md5)')
    input_hashes = {}
    for rel in spec['inputs']:
        p = REPO / rel
        if not p.exists():
            print(f'ERROR: input missing: {rel} — refusing to capture a golden '
                  f'against an absent input.')
            sys.exit(2)
        input_hashes[rel] = md5_file(p)
        print(f'    {input_hashes[rel]}  {rel}')

    try:
        print('[A] running commands on CURRENT code')
        run_commands(spec['commands'])
        print(f'[A] copying outputs -> {a_dir}')
        a_copied = copy_outputs(spec['outputs'], a_dir, 'A')
        missing = [o for o in spec['outputs'] if o not in a_copied]
        if missing:
            print(f'ERROR: phase-A run did not produce: {missing}')
            sys.exit(1)
    finally:
        restore_prod(prod_dir, prod_copied)

    manifest = {
        'target': args.target,
        'commands': [subprocess.list2cmdline(c) for c in spec['commands']],
        'outputs': spec['outputs'],
        'inputs': input_hashes,
        'prod_copied': prod_copied,
        'captured_at': datetime.now().isoformat(timespec='seconds'),
    }
    with open(tdir / 'manifest.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f'\n{OK} phase A complete. Apply your edits, then run --phase B '
          f'(same --target).')
    return 0


def phase_b(args, spec, tdir: Path):
    prod_dir, a_dir = tdir / 'prod', tdir / 'A'
    mpath = tdir / 'manifest.json'
    if not mpath.exists():
        print(f'ERROR: no manifest at {mpath} — run --phase A first for '
              f'--target {args.target}. Refusing to diff against nothing.')
        sys.exit(2)
    with open(mpath, encoding='utf-8') as f:
        manifest = json.load(f)
    if manifest.get('target') != args.target:
        print(f'ERROR: manifest was captured for target {manifest.get("target")!r}, '
              f'not {args.target!r}.')
        sys.exit(2)

    print('[B] verifying input hashes against the phase-A manifest')
    drifted = []
    for rel, want in manifest['inputs'].items():
        p = REPO / rel
        got = md5_file(p) if p.exists() else '<missing>'
        if got != want:
            drifted.append((rel, want, got))
    if drifted:
        print('ERROR: INPUT DRIFT — the A/B diff would measure a data refresh, '
              'not your refactor (the data-coupled-golden lesson). Drifted:')
        for rel, want, got in drifted:
            print(f'    {rel}\n        A: {want}\n        B: {got}')
        print('Re-run --phase A on current code to recapture, then --phase B.')
        sys.exit(2)
    print(f'{OK} all {len(manifest["inputs"])} input hashes match phase A.')

    run_err, results = None, {}
    try:
        print('[B] running commands on EDITED code')
        run_commands(spec['commands'])
        print('\n[B] diffing outputs vs phase A')
        for out in spec['outputs']:
            verdict = diff_output(rel_dest(a_dir, out), REPO / out)
            mark = OK if verdict == 'IDENTICAL' else WARN
            print(f'  {mark} {out}: {verdict}')
            results[out] = verdict
    except RuntimeError as e:
        run_err = str(e)
        print(f'ERROR: {run_err}')
    finally:
        restore_prod(prod_dir, manifest.get('prod_copied', spec['outputs']))

    if run_err:
        return 1
    n_id = sum(1 for v in results.values() if v == 'IDENTICAL')
    print(f'\n[B] verdict: {n_id}/{len(results)} outputs byte-IDENTICAL.')
    if n_id == len(results):
        print(f'{OK} refactor is output-equivalent. Safe to commit.')
        return 0
    print(f'{WARN} NOT byte-identical. EQUIVALENT = pandas-equal (formatting-only '
          f'drift — inspect why serialization changed); DIFFERENT = behavior '
          f'changed. If outputs SHOULD change, that is feature-validation '
          f'territory, not a golden run.')
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--target', choices=[*TARGETS, 'custom'])
    ap.add_argument('--phase', choices=['A', 'B'])
    ap.add_argument('--force', action='store_true', help='override a held lockfile')
    ap.add_argument('--cmd', action='append', help='custom target: command (repeatable)')
    ap.add_argument('--outputs', nargs='+', help='custom target: output paths (repo-relative)')
    ap.add_argument('--inputs', nargs='+', help='custom target: input paths to hash-freeze')
    args = ap.parse_args()

    if not args.target or not args.phase:
        ap.error('--target and --phase are required')

    spec = resolve_target(args)
    root = scratch_root()
    tdir = root / args.target
    tdir.mkdir(parents=True, exist_ok=True)

    lock = root / 'LOCK'
    if lock.exists() and not args.force:
        print(f'ERROR: lockfile present: {lock}\n'
              f'  ({lock.read_text(encoding="utf-8").strip()})\n'
              f'Another golden run — or a concurrent output-writing pipeline — '
              f'would corrupt BOTH phases (outputs rewritten mid-diff, inputs '
              f'drifting under the hashes). If the previous run crashed, '
              f're-run with --force.')
        sys.exit(2)
    lock.write_text(f'pid={os.getpid()} phase={args.phase} target={args.target} '
                    f'started={datetime.now().isoformat(timespec="seconds")}\n',
                    encoding='utf-8')
    try:
        rc = phase_a(args, spec, tdir) if args.phase == 'A' else phase_b(args, spec, tdir)
    finally:
        if lock.exists():
            lock.unlink()
    sys.exit(rc)


if __name__ == '__main__':
    main()
