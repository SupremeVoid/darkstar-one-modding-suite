#!/usr/bin/env python3
"""
dsvalidate — check Darkstar One .3do / .shd files for structural validity.  v1.0

    dsvalidate.py path/to/objects
    dsvalidate.py ship.3do -v

Handles BOTH formats in one tool, because the interesting failures are
usually about the relationship between a .3do and its companion .shd (LOD
counts disagreeing, a shadow mesh left stale after the render mesh changed),
which a per-format validator could not see.

Checks performed
  .3do  - MDL001-MDL007 from dsotools.validate, the same rules and the same
          stable codes the GUI's Problems list shows. This tool used to carry
          its own copy of them with its own thresholds; two implementations of
          one rule is how a CLI and a GUI come to disagree about whether a file
          is broken, so there is now one.
        - re-serialises byte-identically (proves nothing was misread) -- this
          one is the CLI's own, because it is about the *parser*, not the file
        - NaN tangent components (present in stock files too; harmless)
  .shd  - parses cleanly and re-serialises byte-identically
        - indices in range; index width flag consistent with the file size
  pair  - .shd LOD count matches the .3do LOD count (MDL005)
        - warns when a .3do has no .shd (informational: that only means the
          object casts no stencil shadow, which is legal)

Exit code is 0 when nothing failed, 1 otherwise, so it can gate a build.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _dscli as dscli
from dsotools import validate
from dsotools.formats.threedo import parse as parse_3do, build as build_3do
from dsotools.formats import shd as shd_mod


def compare_against_reference(model, ref_path, rep, src):
    """Compare a rebuilt .3do against the stock file it came from and report
    structural drift. Submesh count is the one that matters most: a DCC tool
    that merges submeshes produces a file which passes every self-consistency
    check yet renders wrong in game, because the submesh split is how the
    engine assigns different materials/shaders within one mesh."""
    with open(ref_path, 'rb') as f:
        ref = parse_3do(f.read())
    if len(ref.lods) != len(model.lods):
        rep.anomaly(src, f'LOD count changed: {len(ref.lods)} -> {len(model.lods)}. '
                         f'Lower LODs are used at distance; losing them changes how '
                         f'the object looks far away.')
    for i, (a, b) in enumerate(zip(ref.lods, model.lods)):
        if len(a.submeshes) != len(b.submeshes):
            rep.anomaly(src, f'LOD{i} submesh count changed: {len(a.submeshes)} -> '
                             f'{len(b.submeshes)}. The engine assigns materials per '
                             f'submesh, so merged submeshes break per-part effects '
                             f'(glow/shimmer). In Blender, keep one material slot '
                             f'per submesh.')
        if [str(e) for e in a.elements] != [str(e) for e in b.elements]:
            rep.anomaly(src, f'LOD{i} vertex format changed: {a.format_summary} -> '
                             f'{b.format_summary}')


def add_args(p):
    p.add_argument('-v', '--verbose', action='store_true',
                   help='print each check, not just problems')
    p.add_argument('--no-pairs', action='store_true',
                   help='skip .3do <-> .shd companion checks')
    p.add_argument('--compare', metavar='FILE_OR_DIR',
                   help='compare each .3do against the stock original of the same '
                        'name and report structural drift (submesh/LOD/format changes)')


def check_3do(path, rep, verbose, shadow=None, shadow_path=None):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        raw = f.read()

    # The catalogue rules first, and by their codes, so this prints what the
    # Problems tab prints. An ERROR here means the file is broken; anything
    # else is worth a human look but is not a failure -- the same split the
    # Reporter has always made.
    for d in validate.check_model(raw, path, shadow, shadow_path):
        text = f'[{d.code}] {d.message}'
        if d.severity == validate.Severity.ERROR:
            rep.error(d.path or path, text)
        else:
            rep.anomaly(d.path or path, text)

    model = parse_3do(raw)

    if build_3do(model) != raw:
        rep.anomaly(path, 're-serialises differently from the source; a field may be '
                          'misinterpreted (geometry is probably still fine)')

    for i, lod in enumerate(model.lods):
        nan = sum(1 for v in lod.vertices for c in v.attrs.get((6, 0), ()) if c != c)
        if nan:
            rep.anomaly(path, f'LOD{i}: {nan} NaN tangent components (also present in '
                              f'stock game files; harmless to round-tripping)')

    if verbose:
        print(f'  {name}: {len(model.lods)} LOD(s), '
              f'{sum(len(l.submeshes) for l in model.lods)} submesh(es), '
              f'{model.vertex_count} verts, {model.face_count} tris, '
              f'stride {model.lods[0].stride}')
    return model


def check_shd(path, rep, verbose):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        raw = f.read()
    model = shd_mod.parse(raw)
    if shd_mod.build(model) != raw:
        rep.anomaly(path, 're-serialises differently from the source')
    for i, lod in enumerate(model.lods):
        if len(lod.indices) % 3:
            rep.error(path, f'SLOD{i}: index count not a multiple of 3')
        if lod.indices and max(lod.indices) >= len(lod.vertices):
            rep.error(path, f'SLOD{i}: index out of range')
    if verbose:
        widths = {'32-bit' if l.wide_indices else '16-bit' for l in model.lods}
        print(f'  {name}: {len(model.lods)} SLOD(s), {model.vertex_count} verts, '
              f'{model.face_count} tris, {"/".join(sorted(widths))} indices')
    return model


def main(argv=None):
    parser = dscli.build_parser(__doc__, '.3do/.shd', add_args)
    # validation writes nothing, so mark the shared output options as unused
    for action in parser._actions:
        if {'-o', '--output', '-f', '--force'} & set(action.option_strings):
            action.help = '(unused by this tool)'
    args = parser.parse_args(argv)

    files, _ = dscli.collect_inputs(args.input, ('.3do', '.shd'))
    rep = dscli.Reporter('Validated')

    print(f'Validating {len(files)} file(s):')
    models_3do, models_shd = {}, {}
    for path in files:
        try:
            if path.lower().endswith('.3do'):
                # Pair with the .shd sitting next to it, whether or not that
                # file is itself in this run's list: MDL005 is a fact about the
                # pair, and `dsvalidate ship.3do` should still be told its
                # shadow volume has the wrong number of levels.
                shadow = shadow_path = None
                if not args.no_pairs:
                    sp = path[:-4] + '.shd'
                    if os.path.exists(sp):
                        with open(sp, 'rb') as f:
                            shadow, shadow_path = f.read(), sp
                    elif args.verbose:
                        print(f'  {os.path.basename(path)}: no .shd (object casts no '
                              f'stencil shadow -- legal)')
                m = check_3do(path, rep, args.verbose, shadow, shadow_path)
                models_3do[path[:-4]] = m
                if args.compare:
                    ref = args.compare
                    if os.path.isdir(ref):
                        ref = os.path.join(ref, os.path.basename(path))
                    if os.path.exists(ref):
                        compare_against_reference(m, ref, rep, path)
                    else:
                        rep.anomaly(path, f'no reference file at {ref} to compare against')
            else:
                models_shd[path[:-4]] = check_shd(path, rep, args.verbose)
            rep.ok += 1
        except Exception as e:
            rep.error(path, f'{type(e).__name__}: {e}')

    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
