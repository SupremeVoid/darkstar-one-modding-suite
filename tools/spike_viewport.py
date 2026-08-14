#!/usr/bin/env python3
"""
SPIKE: can Qt Quick 3D carry the Models tab's viewport?

This is a throwaway probe, not a component.  `docs/ARCHITECTURE.md` §2 commits to
PySide6 for the shell but explicitly does *not* commit to Qt Quick 3D for the
3D pane until this runs.  It exists to answer one question -- can a Python
process hand Qt Quick 3D custom `.3do` geometry and decoded DDS textures
without fighting it -- and then be deleted.

    # data pipeline only, no Qt, runs anywhere
    python tools/spike_viewport.py --data <extracted> --check

    # the actual spike, against an installed game (.cpr read directly)
    python tools/spike_viewport.py --game <install> --scene 3DView/PlayerShip.xml

    # neither flag: autodetect the install
    python tools/spike_viewport.py

TWO RISKS, DELIBERATELY SEPARATED
---------------------------------
Risk A is the data: does dsotools produce vertex buffers and RGBA textures that
a GPU can consume?  Risk B is Qt: does Qt Quick 3D accept them from Python?

`--check` runs A alone with no Qt import at all.  If B fails you still know
whether A is sound, which decides whether the fallback (a hand-written
QOpenGLWidget renderer, or three.js in a WebView2 window against the existing
.glb exporter) is a small change or a large one.

WHAT "PASS" MEANS
-----------------
- the ship appears, textured, and orbits smoothly
- adding a second scene does not require new plumbing
- no per-frame Python work is needed to keep it running

Anything else is a fail, and the plan's documented fallbacks apply.

NOT PRODUCTION CODE.  It renders LOD 0 only, ignores CLODSelector, glow objects,
blinkers and shield meshes, assumes the mat_main texture-slot convention, and
has no error recovery worth the name.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dsotools import locate  # noqa: E402
from dsotools import vfs as dsovfs  # noqa: E402
from dsotools.formats import dds, scene, threedo  # noqa: E402

# Slot order for blender/mat_main.bsd9, per specs/scene.md §3.1.  The .bsd9
# format is not decoded, so this is convention, not fact -- which is itself
# something the spike is meant to expose: if a model looks wrong, suspect this
# before suspecting the geometry.
SLOT_BASECOLOR = 0
SLOT_NORMAL = 2

FLOATS_PER_VERTEX = 12  # pos3 + nrm3 + uv2 + tan4

# @QmlElement reads these out of the *module* globals of the decorated class, by
# walking the calling frame -- not out of the enclosing function's locals.  They
# must live here even though the only classes that use them are defined inside
# run_qt(), or the decorator raises "You need specify QML_IMPORT_NAME".
QML_IMPORT_NAME = "DsoSpike"
QML_IMPORT_MAJOR_VERSION = 1


class DrawCall:
    """One submesh: an interleaved vertex buffer, indices, and its maps."""

    __slots__ = ("name", "vertices", "indices", "basecolor", "normalmap", "shader")

    def __init__(self, name, vertices, indices, basecolor, normalmap, shader):
        self.name = name
        self.vertices = vertices      # bytes, FLOATS_PER_VERTEX float32 per vertex
        self.indices = indices        # bytes, uint32
        self.basecolor = basecolor    # (w, h, rgba bytes) or None
        self.normalmap = normalmap
        self.shader = shader

    @property
    def vertex_count(self):
        return len(self.vertices) // (FLOATS_PER_VERTEX * 4)

    @property
    def triangle_count(self):
        return len(self.indices) // 12


def _unswizzle_normal(rgba):
    """Rebuild a tangent-space normal map from DSO's DXT5nm-style storage.

    The ``_nrm`` textures do not hold a normal in RGB.  They hold **X in alpha
    and Y in green**, with Z implied -- the standard DXT5nm trick, which spends
    DXT5's well-interpolated alpha block on the channel that matters and leaves
    RGB carrying nothing usable.

    Measured on ``playership_body_00_nrm.dds``, not assumed: read as plain RGB
    the vectors have median length 0.345, which is not a unit normal by any
    reading; unswizzled, the reconstructed Z has median 0.994, i.e. almost all
    normals point straight out of mostly-flat hull panels, which is exactly what
    a tangent-space map of a spaceship looks like.

    Feeding the raw RGB to a PBR material instead is not a subtle error -- it
    lights every surface from a garbage normal, and the model renders black and
    speckled.  That is what the first run of this spike produced.
    """
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        x = rgba[i + 3] / 127.5 - 1.0
        y = rgba[i + 1] / 127.5 - 1.0
        z = (max(0.0, 1.0 - x * x - y * y)) ** 0.5
        out[i] = rgba[i + 3]
        out[i + 1] = rgba[i + 1]
        out[i + 2] = int(z * 127.5 + 127.5)
        out[i + 3] = 255
    return bytes(out)


def _texture(vfsobj, ref, scene_path, cache, normal=False):
    """Decode one texture reference to (w, h, RGBA bytes), or None."""
    if ref is None:
        return None
    key = (ref.lower(), normal)
    if key in cache:
        return cache[key]
    entry = vfsobj.resolve_reference(ref, scene_path=scene_path)
    out = None
    if entry is not None and entry.vpath.lower().endswith(".dds"):
        try:
            img = dds.parse(entry.read(), path=entry.vpath)
            # Mip 1 rather than 0: a 1024x1024 base is 4 MB of RGBA per texture
            # and a spike does not need it.  Real code streams the base level.
            level = 1 if len(img.levels) > 1 else 0
            surf = img.surface(level)
            rgba = _unswizzle_normal(surf.rgba) if normal else surf.rgba
            out = (surf.width, surf.height, rgba)
        except Exception as exc:  # noqa: BLE001 - spike: report and continue
            print(f"    ! texture {ref}: {exc}")
    cache[key] = out
    return out


def open_vfs(data=None, game_dir=None):
    """Mount the stock data, from an install or from an extracted folder.

    An install is the normal case now: ``.cpr`` is a plain ZIP, so there is no
    extraction step (docs/ARCHITECTURE.md, Phase 0).  ``--data`` stays because a
    machine with no game installed can still exercise the data half.
    """
    if game_dir:
        return dsovfs.from_install(game_dir), f"install {game_dir}"
    if data:
        return dsovfs.from_extracted(data), f"extracted {data}"
    found = locate.find_game()
    if not found:
        raise SystemExit(
            "no game install found -- pass --game <install> or --data <extracted>"
        )
    return dsovfs.from_install(found), f"install {found} (autodetected)"


def build_scene_data(game, scene_path, limit=None, verbose=True):
    """Everything that happens before the GPU.  No Qt imported here."""
    t0 = time.time()
    if verbose:
        print(f"  vfs: {len(game.layers)} layers in {time.time() - t0:.2f}s")

    raw = game.read(scene_path)
    sc = scene.parse(raw, path=scene_path)
    meshes = sc.meshes()
    if verbose:
        print(f"  scene: {scene_path} -- {len(meshes)} meshes")

    calls = []
    texcache = {}
    skipped = 0
    for mesh in meshes:
        if limit and len(calls) >= limit:
            break
        entry = game.resolve_reference(mesh.model, scene_path=scene_path)
        if entry is None:
            skipped += 1
            continue
        try:
            model = threedo.parse(entry.read())
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {mesh.model}: {exc}")
            skipped += 1
            continue

        lod = model.lods[0]
        effects = mesh.effects
        # SCN001: one EffectContainer per submesh across ALL LODs, so LOD 0's
        # submesh i is effect i.  Verified 9,557/9,559 on stock data.
        for i, sub in enumerate(lod.submeshes):
            eff = effects[i] if i < len(effects) else (effects[0] if effects else None)
            textures = eff.textures if eff else []

            vbuf = bytearray()
            lo, hi = sub.vert_start, sub.vert_start + sub.vert_count
            for v in lod.vertices[lo:hi]:
                px, py, pz = v.position
                nx, ny, nz = v.normal
                u, vv = v.uv
                t = v.tangent or (0.0, 0.0, 0.0, 1.0)
                vbuf += struct.pack(
                    "<12f", px, py, pz, nx, ny, nz, u, vv, t[0], t[1], t[2], t[3]
                )

            ibuf = bytearray()
            start = sub.face_start * 3
            for idx in lod.indices[start : start + sub.face_count * 3]:
                # rebase onto the submesh's own vertex slice
                ibuf += struct.pack("<I", max(0, idx - sub.vert_start))

            calls.append(
                DrawCall(
                    name=f"{mesh.name or '?'}[{i}]",
                    vertices=bytes(vbuf),
                    indices=bytes(ibuf),
                    basecolor=_texture(
                        game,
                        textures[SLOT_BASECOLOR] if len(textures) > SLOT_BASECOLOR else None,
                        scene_path,
                        texcache,
                    ),
                    normalmap=_texture(
                        game,
                        textures[SLOT_NORMAL] if len(textures) > SLOT_NORMAL else None,
                        scene_path,
                        texcache,
                        normal=True,
                    ),
                    shader=(eff.shader if eff else None),
                )
            )

    if verbose:
        tris = sum(c.triangle_count for c in calls)
        verts = sum(c.vertex_count for c in calls)
        tex = sum(1 for v in texcache.values() if v)
        print(
            f"  built {len(calls)} draw calls, {verts:,} verts, {tris:,} tris, "
            f"{tex} textures decoded, {skipped} meshes skipped"
        )
        print(f"  total data time: {time.time() - t0:.2f}s")
    return calls


# --------------------------------------------------------------------------
# Qt half
# --------------------------------------------------------------------------

QML = """
import QtQuick
import QtQuick.Window
import QtQuick3D
import DsoSpike

Window {
    width: 1280; height: 800; visible: true
    title: "dsotools spike -- Qt Quick 3D"
    color: "#101014"

    View3D {
        anchors.fill: parent
        environment: SceneEnvironment {
            clearColor: "#101014"
            backgroundMode: SceneEnvironment.Color
            antialiasingMode: SceneEnvironment.MSAA
        }

        PerspectiveCamera {
            id: cam
            position: Qt.vector3d(0, spike.radius * 0.4, spike.radius * 2.6)
            eulerRotation.x: -8
            clipFar: spike.radius * 40
        }

        DirectionalLight { eulerRotation: Qt.vector3d(-30, -70, 0); brightness: 1.4 }
        DirectionalLight { eulerRotation: Qt.vector3d(20, 120, 0);  brightness: 0.5 }

        Node {
            id: pivot
            NumberAnimation on eulerRotation.y {
                from: 0; to: 360; duration: 14000; loops: Animation.Infinite; running: true
            }
            Repeater3D {
                model: spike.calls
                Model {
                    geometry: modelData.geometry
                    materials: PrincipledMaterial {
                        baseColorMap: modelData.baseColorTexture
                            ? tex.createObject(null, {textureData: modelData.baseColorTexture})
                            : null
                        normalMap: modelData.normalTexture
                            ? tex.createObject(null, {textureData: modelData.normalTexture})
                            : null
                        baseColor: modelData.baseColorTexture ? "white" : "#8899aa"
                        metalness: 0.1
                        roughness: 0.55
                    }
                }
            }
        }
    }

    Component { id: tex; Texture {} }

    Text {
        anchors { left: parent.left; top: parent.top; margins: 12 }
        color: "#c8d0dc"
        font.pixelSize: 13
        text: spike.summary
    }
}
"""



def doctor():
    """Delegate to the standalone diagnostic.

    Lives in its own module because the packaged app needs the same logic with
    no console attached (docs/ARCHITECTURE.md), and a spike is the wrong place for
    something with a future.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pyside_doctor

    return pyside_doctor.main()


def run_qt(calls, radius, summary, screenshot=None, seconds=0):
    try:
        from PySide6.QtCore import (
            Property, QByteArray, QObject, QSize, QTimer, QUrl, Signal,
        )
        from PySide6.QtGui import QGuiApplication, QVector3D
        from PySide6.QtQml import QmlElement, QQmlApplicationEngine
        from PySide6.QtQuick3D import QQuick3DGeometry, QQuick3DTextureData
    except ImportError as exc:
        # A raw traceback here says nothing useful.  Diagnose instead.
        print(f"\nPySide6 failed to load: {exc}\n")
        return doctor()

    @QmlElement
    class SubmeshGeometry(QQuick3DGeometry):
        """The whole question, in one class.

        If handing Qt a raw interleaved buffer plus attribute offsets works,
        the viewport is cheap: dsotools already produces exactly this shape.
        """

        def __init__(self, call=None, parent=None):
            super().__init__(parent)
            if call is not None:
                self.load(call)

        def load(self, call):
            stride = FLOATS_PER_VERTEX * 4
            self.setStride(stride)
            self.setVertexData(QByteArray(call.vertices))
            self.setIndexData(QByteArray(call.indices))
            self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
            A = QQuick3DGeometry.Attribute
            self.addAttribute(A.PositionSemantic, 0, A.F32Type)
            self.addAttribute(A.NormalSemantic, 12, A.F32Type)
            self.addAttribute(A.TexCoord0Semantic, 24, A.F32Type)
            self.addAttribute(A.TangentSemantic, 32, A.F32Type)
            self.addAttribute(A.IndexSemantic, 0, A.U32Type)
            xs = [
                struct.unpack_from("<3f", call.vertices, i * stride)
                for i in range(call.vertex_count)
            ]
            if xs:
                mn = [min(p[i] for p in xs) for i in range(3)]
                mx = [max(p[i] for p in xs) for i in range(3)]
                self.setBounds(QVector3D(*mn), QVector3D(*mx))
            self.update()

    @QmlElement
    class RgbaTexture(QQuick3DTextureData):
        def __init__(self, tex=None, parent=None):
            super().__init__(parent)
            if tex is not None:
                w, h, rgba = tex
                self.setSize(QSize(w, h))
                self.setFormat(QQuick3DTextureData.Format.RGBA8)
                self.setHasTransparency(False)
                self.setTextureData(QByteArray(rgba))

    class Call(QObject):
        changed = Signal()

        def __init__(self, call):
            super().__init__()
            self._geom = SubmeshGeometry(call)
            self._base = RgbaTexture(call.basecolor) if call.basecolor else None
            self._norm = RgbaTexture(call.normalmap) if call.normalmap else None

        @Property(QObject, notify=changed)
        def geometry(self):
            return self._geom

        @Property(QObject, notify=changed)
        def baseColorTexture(self):
            return self._base

        @Property(QObject, notify=changed)
        def normalTexture(self):
            return self._norm

    class Spike(QObject):
        changed = Signal()

        def __init__(self, calls, radius, summary):
            super().__init__()
            self._calls = [Call(c) for c in calls]
            self._radius = radius
            self._summary = summary

        @Property("QVariantList", notify=changed)
        def calls(self):
            return self._calls

        @Property(float, notify=changed)
        def radius(self):
            return self._radius

        @Property(str, notify=changed)
        def summary(self):
            return self._summary

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    spike = Spike(calls, radius, summary)
    engine.rootContext().setContextProperty("spike", spike)
    engine.loadData(QML.encode(), QUrl("qrc:/spike.qml"))
    if not engine.rootObjects():
        print("FAIL: QML did not load -- see the errors above.")
        return 2

    # The spike's pass criteria are "it orbits smoothly" and "no per-frame Python
    # work".  Eyeballing a window proves neither, and cannot be recorded.  Count
    # swapped frames instead, and grab the window so the render itself is
    # evidence rather than a claim.
    win = engine.rootObjects()[0]
    stats = {"frames": 0, "first": None, "last": None}

    def on_frame():
        now = time.time()
        if stats["first"] is None:
            stats["first"] = now
        stats["last"] = now
        stats["frames"] += 1

    win.frameSwapped.connect(on_frame)

    def finish():
        span = (stats["last"] or 0) - (stats["first"] or 0)
        fps = (stats["frames"] - 1) / span if span > 0 else 0.0
        print(f"\nrendered {stats['frames']} frames in {span:.1f}s ({fps:.1f} fps)")
        if screenshot:
            img = win.grabWindow()
            if img.isNull() or not img.save(screenshot):
                print(f"  ! could not save {screenshot}")
            else:
                print(f"  saved {screenshot} ({img.width()}x{img.height()})")
        if seconds:
            app.quit()

    if seconds:
        QTimer.singleShot(int(seconds * 1000), finish)
    elif screenshot:
        QTimer.singleShot(3000, finish)

    return app.exec()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--game", help="game install folder (.cpr read directly)")
    ap.add_argument("--data", help="folder of extracted .cpr archives")
    ap.add_argument("--scene", default="3DView/PlayerShip.xml")
    ap.add_argument("--limit", type=int, default=40, help="max meshes (0 = all)")
    ap.add_argument("--check", action="store_true", help="data pipeline only, no Qt")
    ap.add_argument("--screenshot", help="grab the viewport to this PNG")
    ap.add_argument("--no-normal", action="store_true",
                    help="drop the normal map -- the first thing to try when a "
                         "model looks wrong, since the slot order is convention")
    ap.add_argument("--seconds", type=float, default=0,
                    help="run for N seconds then report frame rate and exit")
    ap.add_argument("--doctor", action="store_true", help="diagnose a broken PySide6 install")
    args = ap.parse_args()

    if args.doctor:
        return doctor()

    game, where = open_vfs(data=args.data, game_dir=args.game)
    print(f"source: {where}")
    print(f"scene: {args.scene}")
    calls = build_scene_data(game, args.scene, limit=args.limit or None)
    if args.no_normal:
        for c in calls:
            c.normalmap = None
    if not calls:
        print("FAIL: no drawable geometry produced.")
        return 1

    extent = 0.0
    for c in calls[: min(len(calls), 20)]:
        stride = FLOATS_PER_VERTEX * 4
        for i in range(0, min(c.vertex_count, 500)):
            x, y, z = struct.unpack_from("<3f", c.vertices, i * stride)
            extent = max(extent, abs(x), abs(y), abs(z))
    radius = extent or 10.0

    tris = sum(c.triangle_count for c in calls)
    textured = sum(1 for c in calls if c.basecolor)
    summary = (
        f"{os.path.basename(args.scene)}\n"
        f"{len(calls)} submeshes | {tris:,} triangles | {textured} textured\n"
        f"shaders: {len({c.shader for c in calls if c.shader})}"
    )
    print("\n" + summary.replace("\n", "\n  "))

    if args.check:
        print("\nPASS (data half): geometry and textures built without Qt.")
        print("Risk A is clear; run without --check to test Risk B.")
        return 0

    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("\nPySide6 not installed:  pip install PySide6")
        print("Run with --check to exercise the data half meanwhile.")
        return 1
    except Exception as exc:  # noqa: BLE001 - broken install, not a missing one
        print(f"\nPySide6 present but not loadable: {exc}\n")
        return doctor()
    return run_qt(
        calls, radius, summary, screenshot=args.screenshot, seconds=args.seconds
    )


if __name__ == "__main__":
    sys.exit(main())
