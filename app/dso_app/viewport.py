"""
The 3D viewport: Qt Quick 3D fed custom geometry from Python.

This is the component ``tools/spike_viewport.py`` was written to justify.  The
spike answered the only question that mattered -- can a Python process hand Qt
Quick 3D ``.3do`` geometry and decoded DDS textures without fighting it -- at
144 fps over 830 frames, with a second scene needing no new plumbing.  What
follows is that, made reusable.

TWO THINGS THE SPIKE TAUGHT, BOTH LOAD-BEARING
----------------------------------------------
``@QmlElement`` reads ``QML_IMPORT_NAME`` from the **module** globals by walking
the calling frame, not from the enclosing function's locals.  Defining them
inside a function raises "You need specify QML_IMPORT_NAME" before a window ever
opens, which is why the spike had never actually run to completion.  They are at
module scope here, and must stay there.

``QByteArray`` copies, but the ``bytes`` it is built from must outlive the call
that builds it -- so the geometry keeps a reference to its source buffers.  A
freed buffer renders as garbage intermittently, which is the worst way to meet
a bug.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Property, QByteArray, QObject, QSize, QUrl, Signal
from PySide6.QtGui import QColor, QVector3D
from PySide6.QtQml import QmlElement, QQmlComponent, QQmlEngine
from PySide6.QtQuick3D import QQuick3DGeometry, QQuick3DTextureData
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from dsotools.edit import meshview

# Module scope on purpose -- see the docstring.  Moving these into a function
# breaks every @QmlElement below.
QML_IMPORT_NAME = "DsoModels"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class SubmeshGeometry(QQuick3DGeometry):
    """One submesh, handed to the GPU as an interleaved buffer.

    The whole viewport rests on this class: ``dsotools`` already produces
    exactly this shape, so there is no conversion layer between the format code
    and the renderer.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._vertices = b""
        self._indices = b""

    def load(self, call: "meshview.DrawCall") -> "SubmeshGeometry":
        # Held so the buffers outlive the QByteArray construction below.
        self._vertices = call.vertices
        self._indices = call.indices

        self.setStride(meshview.STRIDE)
        self.setVertexData(QByteArray(self._vertices))
        self.setIndexData(QByteArray(self._indices))
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)

        attr = QQuick3DGeometry.Attribute
        offsets = meshview.ATTRIBUTE_OFFSETS
        self.addAttribute(attr.PositionSemantic, offsets["position"], attr.F32Type)
        self.addAttribute(attr.NormalSemantic, offsets["normal"], attr.F32Type)
        self.addAttribute(attr.TexCoord0Semantic, offsets["uv"], attr.F32Type)
        self.addAttribute(attr.TangentSemantic, offsets["tangent"], attr.F32Type)
        self.addAttribute(attr.IndexSemantic, 0, attr.U32Type)

        box = call.bounds()
        if box is not None:
            self.setBounds(QVector3D(*box[0]), QVector3D(*box[1]))
        self.update()
        return self


@QmlElement
class RgbaTexture(QQuick3DTextureData):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rgba = b""

    def load(self, texture: "meshview.Texture") -> "RgbaTexture":
        self._rgba = texture.rgba
        self.setSize(QSize(texture.width, texture.height))
        self.setFormat(QQuick3DTextureData.Format.RGBA8)
        self.setHasTransparency(False)
        self.setTextureData(QByteArray(self._rgba))
        return self


#: How solid each layer is drawn.  The engine blends glow, shine, distortion
#: and shield additively over the hull; Qt Quick 3D draws whatever we give it as
#: ordinary triangles, so without this a shield bubble is an opaque shell with
#: the ship sealed inside.  Anything unlisted is fully opaque.
LAYER_OPACITY = {
    meshview.LAYER_SHIELD: 0.60,
    meshview.LAYER_GLOW: 0.55,
    meshview.LAYER_SHINE: 0.55,
    meshview.LAYER_DISTORTION: 0.50,
}

#: **The `<Material>` "emissive" row is not self-illumination**, and driving
#: Qt's ``emissiveFactor`` from it was wrong.
#:
#: Two pieces of evidence, and they agree.  Measured: **5,069 of 16,444**
#: shipped materials set that row to (1,1,1).  If it meant self-illumination,
#: a third of everything in the game would render as a featureless white blob,
#: and it does not.  Rendered: ``ComSat``'s hull came out pure white -- mean
#: (244,244,244), 92% of lit pixels near-white -- and dropped to a correctly
#: textured (129,130,133) the moment emissive was forced to zero.
#:
#: So a submesh **with** a base colour map now gets no emissive at all: the
#: texture is the thing worth showing.  One **without** still uses the row,
#: because it is the only description of the surface there is -- and because
#: the blinker markers are exactly that case.
#:
#: This is a correction to the provisional D3DMATERIAL9 row reading, not a
#: rendering tweak; see ``specs/bsd9.md`` §5.
EMISSIVE_NEEDS_NO_TEXTURE = True


class CallItem(QObject):
    """One draw call, as QML sees it."""

    changed = Signal()

    def __init__(self, call: "meshview.DrawCall") -> None:
        super().__init__()
        # **Python owns this, not the JavaScript engine.**
        #
        # A QObject handed to QML with no parent gets JavaScriptOwnership, so
        # QML is free to collect it.  That never bit while `calls` was set once
        # per scene -- the Repeater built its delegates and nothing triggered a
        # sweep.  Adding the blinker overlay changes the list *after* the scene
        # is up, the Repeater rebuilds every delegate, and the next collection
        # took the C++ side of every other CallItem with it: the viewport went
        # black except for the one submesh that had just been created.
        #
        # Same family as the texture bug in `show_geometry`'s history -- an
        # object with no owner on the C++ side is an object with a deadline.
        QQmlEngine.setObjectOwnership(self, QQmlEngine.ObjectOwnership.CppOwnership)
        # And the same for the geometry and the textures, which are handed to
        # QML through properties of their own.  It was *these* the sweep was
        # taking, not the CallItem: a Model whose geometry has been collected
        # draws nothing at all, which is why the viewport went black.
        #
        # Ownership, not parenting.  `QQuick3DGeometry` and
        # `QQuick3DTextureData` are `QQuick3DObject`s and expect a scene-graph
        # parent; handing them a plain QObject leaves the whole scene empty.
        self._geometry = SubmeshGeometry().load(call)
        self._base = RgbaTexture().load(call.basecolor) if call.basecolor else None
        self._normal = RgbaTexture().load(call.normalmap) if call.normalmap else None
        for owned in (self._geometry, self._base, self._normal):
            if owned is not None:
                QQmlEngine.setObjectOwnership(
                    owned, QQmlEngine.ObjectOwnership.CppOwnership
                )
        # Two independent reasons to be hidden, kept apart so neither clobbers
        # the other: the variant selector, and "isolate this submesh".
        self._in_selection = True
        self._isolated_ok = True
        #: Overlays answer a question the user just asked (which blinker is
        #: this row?), so the variant selector and the isolate button leave
        #: them alone -- but the layer switch does not.  Unticking `blinker`
        #: with a row selected has to make the marker go away, or the switch
        #: appears broken.
        self.is_overlay = False
        self.call = call

    @Property(QObject, notify=changed)
    def geometry(self):
        return self._geometry

    @Property(QObject, notify=changed)
    def baseColorTexture(self):
        return self._base

    @Property(QObject, notify=changed)
    def normalTexture(self):
        return self._normal

    # -- shading, from the scene's own numbers --------------------------------
    #
    # Best effort, and labelled as such in the UI.  .bsd9 is undecoded, so what
    # the engine does with these is unknown -- but the file's own Roughness is
    # a better guess than a hard-coded 0.55, and it means editing a value in
    # the effect editor visibly changes something instead of appearing to do
    # nothing at all.

    @staticmethod
    def _clamped(parameters, name, fallback):
        value = parameters.get(name)
        if value is None:
            return fallback
        return max(0.0, min(1.0, float(value)))

    @Property(float, notify=changed)
    def roughness(self):
        return self._clamped(self.call.parameters, "Roughness", 0.55)

    @Property(float, notify=changed)
    def metalness(self):
        return self._clamped(self.call.parameters, "Reflectivity", 0.1)

    @Property(QColor, notify=changed)
    def diffuse(self):
        """The material's diffuse row, used when there is no base texture."""
        m = self.call.material
        if len(m) < 4:
            return QColor("#8899aa")
        return QColor.fromRgbF(
            max(0.0, min(1.0, m[0])),
            max(0.0, min(1.0, m[1])),
            max(0.0, min(1.0, m[2])),
        )

    @Property(float, notify=changed)
    def emissive(self):
        """Self-illumination -- **only when there is no texture to show.**

        The `<Material>` row read as "emissive" cannot be self-illumination in
        the engine's sense; see :data:`EMISSIVE_NEEDS_NO_TEXTURE`.  Where a base
        colour map exists it wins outright, because showing the artist's texture
        is the entire job.  Where there is none the row is all there is, so it
        is used -- which is also what keeps the blinker markers bright.
        """
        if self._base is not None:
            return 0.0
        m = self.call.material
        if len(m) < 16:
            return 0.0
        strength = (m[12] + m[13] + m[14]) / 3.0
        factor = self.call.parameters.get("EmissiveFactor")
        return max(0.0, min(1.0, strength * (1.0 if factor is None else factor)))

    @Property(float, notify=changed)
    def opacity(self):
        """How solid to draw this submesh.

        Geometry and collision are opaque; the additive shells are not.  The
        shield is the extreme case -- a bubble enclosing the whole ship, which
        drawn solid hides everything it is wrapped around.
        """
        return LAYER_OPACITY.get(self.call.layer, 1.0)

    @Property(bool, notify=changed)
    def shown(self):
        return self._in_selection and self._isolated_ok

    def replace_geometry(self, call) -> None:
        """Swap in new geometry for the same submesh, in place.

        Only the geometry object changes; the CallItem stays put, so `calls` is
        untouched and `Repeater3D` does not rebuild -- which is the thing that
        blanked the viewport last time.
        """
        self.call = call
        self._geometry = SubmeshGeometry().load(call)
        QQmlEngine.setObjectOwnership(
            self._geometry, QQmlEngine.ObjectOwnership.CppOwnership
        )
        self.changed.emit()

    def set_shading(self, parameters, material) -> None:
        """Re-shade from new numbers, without touching geometry or textures.

        Every shading property above reads straight off ``self.call`` and is
        declared ``notify=changed``, so swapping the values and emitting once
        is the whole of a live preview -- no rebuild, no re-upload of the
        70,000 triangles and their textures.

        ``None`` for either leaves that half alone.
        """
        if parameters is not None:
            self.call.parameters = dict(parameters)
        if material is not None:
            self.call.material = tuple(material)
        self.changed.emit()

    def set_in_selection(self, value: bool) -> None:
        if self._in_selection != value:
            self._in_selection = value
            self.changed.emit()

    def set_isolated_ok(self, value: bool) -> None:
        if self._isolated_ok != value:
            self._isolated_ok = value
            self.changed.emit()


class ViewportModel(QObject):
    """The context object the QML scene binds to."""

    changed = Signal()
    #: Separate from `changed` on purpose: emitting `changed` re-reads `calls`
    #: and rebuilds every delegate, which is the bug this signal exists to
    #: avoid.
    highlightChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._calls: List[CallItem] = []
        self._highlight: Optional[CallItem] = None
        self._radius = 10.0
        self._center = QVector3D(0, 0, 0)

    @Property("QVariantList", notify=changed)
    def calls(self):
        return self._calls

    @Property(float, notify=changed)
    def radius(self):
        return self._radius

    @Property(QVector3D, notify=changed)
    def center(self):
        """The bounding box centre.  Models are rarely built around the origin,
        so orbiting about (0,0,0) swings the subject around the screen."""
        return self._center

    @Property(QObject, notify=highlightChanged)
    def highlight(self):
        return self._highlight

    def set_overlay(self, call) -> None:
        """Show one extra draw call, outside the variant and isolate filters.

        The marker answers "which row is this?", so hiding it because a
        *different* submesh is isolated would be wrong.  Its own layer switch
        still applies -- see :meth:`ModelViewport.apply_visibility`.

        **Its own property, not an extra entry in `calls`.**  Appending to the
        list replaced it, which made `Repeater3D` rebuild every delegate -- and
        destroying a `Model` destroys the `QQuick3DGeometry` it was given, so
        every other submesh lost its geometry and the viewport went black
        except for the marker.  Nothing here touches `calls`.
        """
        self._highlight = CallItem(call) if call is not None else None
        if self._highlight is not None:
            self._highlight.is_overlay = True
        self.highlightChanged.emit()

    def set_geometry(self, geometry: Optional["meshview.SceneGeometry"],
                     layers=meshview.DEFAULT_LAYERS) -> None:
        """Upload the scene, framing the camera on ``layers``.

        Framing deliberately ignores the layers that are switched off.  Glow
        and shield hulls reach well past the ship, so counting them would shrink
        every model on screen to make room for shells nobody asked to see.
        """
        self._calls = [CallItem(c) for c in geometry.calls] if geometry else []
        self._radius = geometry.radius(layers) if geometry else 10.0
        self._center = (
            QVector3D(*geometry.center(layers)) if geometry else QVector3D(0, 0, 0)
        )
        self.changed.emit()


QML = """
import QtQuick
import QtQuick3D
import DsoModels

// The orbit rig is hand-rolled rather than QtQuick3D.Helpers'
// OrbitCameraController.  That plugin's DLL fails to load in the PySide6 wheel
// ("Cannot load library qtquick3dhelpersplugin.dll"), and a viewport that
// depends on it would be one bad wheel away from a black rectangle in the
// packaged build.  Forty lines of QML is the cheaper dependency.
Item {
    id: root

    // Three-quarter view, not dead-on.  Ships are long and thin -- framing
    // one end-on shows a small cross-section and wastes the viewport.
    property real yaw: 35
    property real pitch: -18
    property real distance: viewport.radius * 2.1
    property vector3d target: viewport.center

    onDistanceChanged: if (distance < viewport.radius * 0.15)
                           distance = viewport.radius * 0.15

    View3D {
        id: view
        anchors.fill: parent
        environment: SceneEnvironment {
            clearColor: "#101014"
            backgroundMode: SceneEnvironment.Color
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
        }

        // Classic orbit rig: rotate the parent, park the camera back along +Z.
        // Rotating the camera itself instead makes panning and zooming fight
        // each other as soon as the target moves off the origin.
        Node {
            position: root.target
            eulerRotation: Qt.vector3d(root.pitch, root.yaw, 0)
            PerspectiveCamera {
                id: camera
                position: Qt.vector3d(0, 0, root.distance)
                clipNear: Math.max(0.05, viewport.radius * 0.005)
                clipFar: Math.max(100, viewport.radius * 80)
            }
        }

        // Three lights, because one makes every surface facing away from it a
        // flat silhouette and "is that geometry missing or unlit?" is exactly
        // the question this viewport exists to answer.
        DirectionalLight { eulerRotation: Qt.vector3d(-30, -70, 0); brightness: 1.3 }
        DirectionalLight { eulerRotation: Qt.vector3d(20, 120, 0);  brightness: 0.6 }
        DirectionalLight { eulerRotation: Qt.vector3d(80, 0, 0);    brightness: 0.3 }

        Node {
            id: pivot
            Repeater3D {
                model: viewport.calls
                Model {
                    visible: modelData.shown
                    geometry: modelData.geometry

                    // Declared as children, NOT createObject(null, ...).
                    //
                    // An object created with a null parent is owned by the
                    // JavaScript engine, so the next garbage collection frees
                    // it and every material silently loses its maps -- the
                    // model turns flat grey. Camera movement is what triggers
                    // the collection, which made it look like a rendering bug
                    // rather than a lifetime one. Measured: 1207 coloured
                    // pixels before engine.collectGarbage(), 0 after.
                    //
                    // Declaring them here parents them to the Model, so they
                    // live and die with it.
                    Texture { id: baseMap; textureData: modelData.baseColorTexture }
                    Texture { id: normMap; textureData: modelData.normalTexture }

                    materials: PrincipledMaterial {
                        baseColorMap: modelData.baseColorTexture ? baseMap : null
                        normalMap: modelData.normalTexture ? normMap : null
                        baseColor: modelData.baseColorTexture
                            ? "white" : modelData.diffuse
                        metalness: modelData.metalness
                        roughness: modelData.roughness
                        emissiveFactor: Qt.vector3d(modelData.emissive,
                                                    modelData.emissive,
                                                    modelData.emissive)
                        // Shells the engine blends additively over the hull --
                        // shield bubbles above all -- are opaque triangles as
                        // far as Qt Quick 3D is concerned, so a shield mesh
                        // simply swallowed the ship inside it.  Drawn
                        // see-through instead, which is nearer what the engine
                        // does and, more to the point, lets you see what you
                        // are editing.
                        opacity: modelData.opacity
                        alphaMode: modelData.opacity < 1.0
                            ? PrincipledMaterial.Blend
                            : PrincipledMaterial.Default
                    }
                }
            }

            // The blinker highlight, deliberately OUTSIDE the Repeater.
            //
            // It used to be appended to `calls`, which replaced the list and
            // made Repeater3D rebuild every delegate -- and rebuilding
            // destroys the old Models, taking the QQuick3DGeometry each one had
            // been given with them.  The result was a viewport containing
            // nothing but the marker that had just been created.  A property of
            // its own leaves `calls` untouched, so nothing is rebuilt.
            Model {
                property var hl: viewport.highlight
                visible: hl !== null && hl.shown
                geometry: hl ? hl.geometry : null
                materials: PrincipledMaterial {
                    // A literal, not the draw call's diffuse row: the marker's
                    // colour is the app's, not the scene's, and it has to stay
                    // distinct from every white blinker around it.
                    baseColor: "#ff2020"
                    lighting: PrincipledMaterial.NoLighting
                }
            }
        }

    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
        property real lastX: 0
        property real lastY: 0

        onPressed: (mouse) => { lastX = mouse.x; lastY = mouse.y }
        onPositionChanged: (mouse) => {
            var dx = mouse.x - lastX
            var dy = mouse.y - lastY
            lastX = mouse.x
            lastY = mouse.y
            if (mouse.buttons & Qt.LeftButton) {
                root.yaw -= dx * 0.4
                // Clamped just short of vertical: at exactly +/-90 the rig
                // gimbal-locks and the model appears to spin on its own.
                root.pitch = Math.max(-89, Math.min(89, root.pitch - dy * 0.4))
            } else if (mouse.buttons & (Qt.MiddleButton | Qt.RightButton)) {
                var scale = root.distance * 0.0015
                root.target = Qt.vector3d(
                    root.target.x - dx * scale,
                    root.target.y + dy * scale,
                    root.target.z)
            }
        }
        onWheel: (wheel) => {
            root.distance *= wheel.angleDelta.y > 0 ? 0.88 : 1.136
        }
    }

    function resetView() {
        root.yaw = 35
        root.pitch = -18
        root.target = viewport.center
        root.distance = viewport.radius * 2.1
    }
}
"""


class ModelViewport(QWidget):
    """A QQuickWidget hosting the Qt Quick 3D scene, in a plain widget layout."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout

        self.model = ViewportModel()
        self.quick = QQuickWidget(self)
        self.quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.quick.rootContext().setContextProperty("viewport", self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.quick)

        self._loaded = False
        self._errors: List[str] = []
        self._last_selection = None
        self._last_layers = None
        #: Which blinker group the editor has open.  Only that group's markers
        #: are drawn -- a scene can hold 63 of them and showing all at once is
        #: a cloud of dots with no way to tell which table they belong to.
        self._blinker_group: Optional[str] = None

    def ensure_loaded(self) -> bool:
        """Load the QML on first use, and report failure instead of hiding it.

        Built from a string through ``QQmlComponent.setData`` rather than a
        file, because ``QQuickWidget`` has no ``loadData`` and shipping a
        loose .qml alongside a frozen one-folder build is one more thing that
        can go missing.  ``setContent`` is the documented pairing.
        """
        if self._loaded:
            return True

        url = QUrl("qrc:/dso/viewport.qml")
        self._component = QQmlComponent(self.quick.engine())
        self._component.setData(QML.encode(), url)
        if self._component.isError():
            self._errors = [e.toString() for e in self._component.errors()]
            return False

        item = self._component.create()
        if item is None:
            self._errors = [e.toString() for e in self._component.errors()] or [
                "the QML root object could not be created"
            ]
            return False

        self.quick.setContent(url, self._component, item)
        if self.quick.status() == QQuickWidget.Status.Error:
            self._errors = [e.toString() for e in self.quick.errors()]
            return False
        self._loaded = True
        return True

    @property
    def errors(self) -> List[str]:
        return list(self._errors)

    def show_geometry(self, geometry) -> None:
        self._geometry = geometry
        self.model.set_geometry(geometry)

    def apply_visibility(self, selection, layers=None) -> None:
        """Hide the variants not chosen and the layers switched off."""
        self._last_selection, self._last_layers = selection, layers
        geometry = getattr(self, "_geometry", None)
        if geometry is None:
            return
        visible = {id(c) for c in geometry.visible_calls(selection, layers)}
        allowed = None if layers is None else set(layers)
        for item in self.model.calls:
            if item.call.layer == meshview.LAYER_BLINKER:
                # Markers are editor affordances, not scene content: the
                # variant selector has nothing to say about them, and matching
                # them by object identity broke the moment a group's geometry
                # was re-cut from the table (the new DrawCall is a different
                # object, so it fell out of `visible` and vanished).  Their
                # rule is simply: the layer is on, and this is the group the
                # table has open.
                item.set_in_selection(
                    (allowed is None or item.call.layer in allowed)
                    and (self._blinker_group is None
                         or item.call.node_path == self._blinker_group)
                )
                continue
            item.set_in_selection(id(item.call) in visible)
        # The marker is not filtered by variant or isolate -- it answers a
        # question about a table row -- but its layer still counts: unticking
        # `blinker` has to clear it, or the switch looks broken.
        hl = self.model.highlight
        if hl is not None:
            allowed = None if layers is None else set(layers)
            hl.set_in_selection(allowed is None or hl.call.layer in allowed)

    def apply_selection(self, selection) -> None:
        """Hide the variants the selector did not choose.

        Toggling visibility rather than rebuilding: the geometry and textures
        are already uploaded, and re-uploading 70,000 triangles to change which
        hull is on screen would make the selector feel broken.
        """
        geometry = getattr(self, "_geometry", None)
        if geometry is None:
            return
        visible = {id(c) for c in geometry.visible_calls(selection)}
        for item in self.model.calls:
            if item.is_overlay:
                continue
            item.set_in_selection(id(item.call) in visible)

    def set_blinker_group(self, node_path: Optional[str]) -> None:
        """Show only this group's markers."""
        self._blinker_group = node_path
        self.apply_visibility(self._last_selection, self._last_layers)

    def update_blinker_group(self, node_path: str, blinkers) -> bool:
        """Re-cut one group's white markers from ``[(position, size)]``.

        Called while the table is being edited, so the markers follow the
        numbers rather than the file.  Returns whether a group was found.
        """
        for item in self.model.calls:
            if (item.call.layer == meshview.LAYER_BLINKER
                    and item.call.node_path == node_path):
                call = meshview.blinker_marker_call(
                    item.call.node, node_path,
                    item.call.textures[0] if item.call.textures else None,
                    blinkers,
                )
                if call is None:
                    # Every blinker deleted: nothing to draw, so hide it
                    # rather than leave the old cluster behind.
                    item.set_in_selection(False)
                    return True
                item.replace_geometry(call)
                return True
        return False

    def show_blinker(self, position=None, size: float = 0.2) -> None:
        """Mark one blinker in red, or clear the mark when ``position`` is None."""
        call = (
            meshview.blinker_highlight(position, size)
            if position is not None else None
        )
        self.model.set_overlay(call)

    def preview_shading(self, call, parameters=None, material=None) -> bool:
        """Shade one submesh with values that are not saved yet.

        Matched on the draw call's **identity**, not on a row index: the parts
        table lists only the *visible* calls while the viewport holds all of
        them, so the two are not the same sequence whenever a variant is
        hidden.  Returns whether a submesh was found.
        """
        for item in self.model.calls:
            if item.call is call:
                item.set_shading(parameters, material)
                return True
        return False

    def set_isolated(self, call) -> None:
        """Show one submesh alone, or all of them when ``call`` is ``None``.

        Matched on the **draw call itself**, never on a row index.  This took a
        row index once, and the two sequences are not the same one: the parts
        table lists only the *visible* calls while the viewport holds all of
        them -- 10 against 254 on ``PlayerShip``.  So clicking `wing_00_`, row 7
        of the table, isolated call 7 of the viewport, which is a submesh of a
        body variant the variant selector has hidden.  A hidden call cannot be
        shown by isolating it, so the result was an entirely black viewport with
        the object model reporting nothing wrong -- the same wrong key, and the
        same silence, as the by-name mesh lookup in `session.mesh_for`.
        """
        for item in self.model.calls:
            if item.is_overlay:
                continue
            item.set_isolated_ok(call is None or item.call is call)

    def reset_view(self) -> None:
        root = self.quick.rootObject()
        if root is not None:
            root.resetView()

    def view_state(self):
        """Camera orbit, so a reload can put it back.

        Saving an edit reopens the scene to prove what was written; snapping
        the camera back to the default each time makes every Apply feel like
        the tab reset itself.
        """
        root = self.quick.rootObject()
        if root is None:
            return None
        return {
            "yaw": root.property("yaw"),
            "pitch": root.property("pitch"),
            "distance": root.property("distance"),
            "target": root.property("target"),
        }

    def restore_view_state(self, state) -> None:
        root = self.quick.rootObject()
        if root is None or not state:
            return
        for name, value in state.items():
            if value is not None:
                root.setProperty(name, value)


__all__ = ["ModelViewport", "ViewportModel", "SubmeshGeometry", "RgbaTexture"]
