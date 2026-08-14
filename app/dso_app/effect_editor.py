"""
Shader options for one ``EffectContainer``: shader, parameters, material.

WHAT THE SHADER SETTLES, AND WHAT IT DOES NOT
---------------------------------------------
``.bsd9`` is decoded (``specs/bsd9.md``), and three things follow for this
widget:

- **The parameter names are real.**  Each is a semantic the shader declares,
  with a type and a **compiled-in default** -- which is what "Reset to shader
  defaults" resets to.  Before the decode there was no defensible value to
  offer and the button could not have existed.
- **A parameter the shader does not declare is inert.**  The exporter wrote the
  same fixed block onto every effect regardless, so 17,998 of 82,872 parameter
  writes in stock data land nowhere.  Those rows are marked; editing one
  changes the file and nothing on screen.
- **``<Material>`` is confirmed in shape, not in order.**  ``mat_main`` declares
  ``Diffuse``, ``Specular``, ``Ambient`` and ``Emissive`` as 1x4 vectors plus a
  scalar ``SpecularPower`` -- 4x4 + 1 = 17, exactly the block's size.  But D3DX
  binds by *semantic*, not by position, so the shader cannot say which row is
  which in the scene XML.  The row labels stay provisional and the widget still
  says so on screen; ``specs/bsd9.md`` §5 has the corpus evidence behind the
  order used here.

- **The texture slots have names.**  ``slot_names`` comes from the same
  decode, so a slot can be labelled ``DiffuseMap`` instead of ``0`` -- which is
  what makes rebinding one a decision rather than a guess.

Values are edited as numbers, which is what they demonstrably are.

REBINDING A SLOT
----------------
The scene names its textures **relative to itself**, and the translation from a
virtual path to that spelling lives in ``Vfs.reference_for``, not here.  This
widget deals only in vpaths and hands them to ``Session.set_effect``; a texture
that cannot be named from this scene is refused there rather than written in a
form that resolves to nothing.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import theme
from .session import effect_default_values, effect_parameter_edits

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: The four rows of a D3DMATERIAL9, in order.  Provisional -- see the docstring.
MATERIAL_ROWS = ("diffuse", "ambient", "specular", "emissive")


class EffectEditor(QWidget):
    """Shader path, named parameters, and the 17-float material.

    ``on_apply(shader, parameters, material)`` is called with only what
    changed; ``None`` means "leave alone".
    """

    def __init__(self, on_apply: Callable[..., None], *,
                 embedded: bool = False,
                 on_preview: Optional[Callable[..., None]] = None,
                 on_pick_texture: Optional[Callable[[int], Optional[str]]] = None
                 ) -> None:
        super().__init__()
        self._on_apply = on_apply
        #: Asked for a vpath when the author wants to rebind slot *n*.  A
        #: callback rather than a dialog built here: choosing a texture is a
        #: browse over the whole VFS, which this widget has no business
        #: knowing about.
        self._on_pick_texture = on_pick_texture
        #: Called with ``(parameters, material)`` on every edit, so the viewport
        #: can shade with the values being typed rather than waiting for Apply.
        #: Both are complete dicts/lists, not deltas -- a preview is a whole
        #: state, and diffing it against the loaded effect is Apply's job.
        self._on_preview = on_preview
        self._loaded: Optional[dict] = None
        self._param_boxes: Dict[str, QDoubleSpinBox] = {}
        self._material_boxes: List[QDoubleSpinBox] = []
        #: ``{slot: vpath}`` the author has changed, empty until they do.
        #: Kept as vpaths -- the scene-relative spelling is worked out at write
        #: time, where the scene path is known and can be proved by resolving.
        self._texture_edits: Dict[int, str] = {}
        self._texture_labels: Dict[int, QLabel] = {}
        #: Suppresses preview callbacks while the boxes are being built or
        #: reset, so rebuilding a form does not fire one signal per widget.
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.box = QGroupBox("Shader options")
        layout = QVBoxLayout(self.box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Shader:"))
        self.shader_edit = QLineEdit()
        self.shader_edit.setPlaceholderText("blender/mat_main.bsd9")
        row.addWidget(self.shader_edit, 1)
        # Two different "put it back" buttons, because they mean two different
        # things and conflating them is confusing in exactly one direction:
        # someone reaches for "defaults" wanting the values they started with.
        self.btn_revert = QPushButton("Undo my changes")
        self.btn_revert.setToolTip(
            "Put every value back to what this scene currently says — "
            "the values shown when this dialog opened."
        )
        self.btn_revert.clicked.connect(self._revert)
        row.addWidget(self.btn_revert)

        self.btn_defaults = QPushButton("Reset to shader defaults")
        self.btn_defaults.clicked.connect(self._reset_to_defaults)
        row.addWidget(self.btn_defaults)

        self.btn_apply = QPushButton("Apply to mod")
        self.btn_apply.clicked.connect(self._apply)
        # "Undo my changes" is useful in the dialog too -- Cancel throws the
        # edit away *and* closes, which is not the same thing.
        if not embedded:
            row.addWidget(self.btn_apply)
        else:
            self.btn_apply.hide()
        layout.addLayout(row)

        # Side by side, not stacked: six parameters above five material rows is
        # ~11 rows of spin boxes, which pushes the 3D viewport down to a strip.
        columns = QHBoxLayout()
        self.params_form = QFormLayout()
        self.material_grid = QGridLayout()
        columns.addLayout(self.params_form, 1)
        columns.addLayout(self.material_grid, 2)
        layout.addLayout(columns)

        self.textures_box = QGroupBox("Textures")
        self.textures_form = QFormLayout(self.textures_box)
        layout.addWidget(self.textures_box)

        note = QLabel(
            "Material rows are read as D3DMATERIAL9 — diffuse, ambient, "
            "specular, emissive, then specular power. The shader declares all "
            "five, so the shape is confirmed; the row order is not, because "
            "D3DX binds by semantic rather than by position."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        outer.addWidget(self.box)
        self.setVisible(False)

    # -- contents ------------------------------------------------------------

    def load(self, effect: Optional[dict]) -> None:
        """``effect`` is one entry from ``Session.scene_detail`` ``slots``."""
        self._loaded = effect
        self.setVisible(effect is not None)
        if effect is None:
            return

        self._loading = True
        try:
            self.shader_edit.setText(effect.get("shader") or "")
            self._build_params(effect.get("parameters") or {}, effect.get("semantics"))
            self._build_material(list(effect.get("material") or ()))
            self._texture_edits = {}
            self._build_textures(effect)
        finally:
            self._loading = False

        # Only offer the reset when there is something real to reset *to*.
        # Before `.bsd9` was decoded there was not, and a button that invents
        # numbers is worse than no button.
        defaults = effect.get("defaults")
        material_default = effect.get("material_default")
        self.btn_defaults.setEnabled(bool(defaults) or bool(material_default))
        self.btn_defaults.setToolTip(
            "Set every value to the default compiled into the shader itself."
            if defaults or material_default
            else "This shader could not be read, so its defaults are unknown."
        )

    def _build_textures(self, effect: dict) -> None:
        """One row per texture slot: what it is, what it binds, and a way in.

        The slot *name* is the point.  ``slot_names`` is what the ``.bsd9``
        declares, so a row can say ``DiffuseMap`` rather than ``slot 0`` -- and
        without that, rebinding is picking a number out of the air.  When the
        shader could not be read the rows fall back to the index and say so,
        rather than inventing a name.
        """
        self._clear(self.textures_form)
        self._texture_labels = {}

        references = list(effect.get("textures") or ())
        vpaths = list(effect.get("texture_vpaths") or ())
        resolved = list(effect.get("resolved") or ())
        names = effect.get("slot_names")

        self.textures_box.setVisible(bool(references))
        if not references:
            return

        for slot, reference in enumerate(references):
            label = (names[slot] if names and slot < len(names)
                     else f"slot {slot}")
            row = QHBoxLayout()
            shown = QLabel()
            shown.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self._texture_labels[slot] = shown
            ok = resolved[slot] if slot < len(resolved) else False
            self._show_texture(slot, reference, ok)

            button = QPushButton("Change…")
            button.setEnabled(self._on_pick_texture is not None)
            button.clicked.connect(
                lambda _c=False, s=slot: self._pick_texture(s))
            row.addWidget(shown, 1)
            row.addWidget(button)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.textures_form.addRow(f"{label}:", holder)
            if slot < len(vpaths) and vpaths[slot]:
                shown.setToolTip(vpaths[slot])

    def _show_texture(self, slot: int, text: str, resolved: bool) -> None:
        shown = self._texture_labels.get(slot)
        if shown is None:
            return
        if resolved:
            shown.setText(text)
            shown.setStyleSheet("")
        else:
            # A binding that resolves to nothing is the failure this whole
            # panel exists to surface; it must not look like an ordinary row.
            shown.setText(f"{text}  — resolves to nothing")
            shown.setStyleSheet(f"color: {theme.SEVERITY['warning']};")

    def _pick_texture(self, slot: int) -> None:
        if self._on_pick_texture is None:
            return
        chosen = self._on_pick_texture(slot)
        if not chosen:
            return
        self._texture_edits[slot] = chosen
        self._show_texture(slot, chosen, True)

    def _changed(self) -> None:
        """One edit; push the whole current state to the preview."""
        if self._loading or self._on_preview is None or self._loaded is None:
            return
        self._on_preview(self.current_parameters(), self.current_material())

    def current_parameters(self) -> Dict[str, Optional[float]]:
        """Every parameter as it stands, with ``None`` for "shader default"."""
        out: Dict[str, Optional[float]] = {}
        for name, spin in self._param_boxes.items():
            out[name] = None if spin.value() == spin.minimum() else spin.value()
        return out

    def current_material(self) -> Optional[List[float]]:
        if not self._material_boxes:
            return None
        return [s.value() for s in self._material_boxes]

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_params(self, parameters: Dict[str, Optional[float]],
                      semantics: Optional[List[str]] = None) -> None:
        """``semantics`` is what the `.bsd9` declares, or ``None`` if unread.

        The exporter wrote the same fixed parameter block onto every effect
        regardless of the shader, so a fifth of the values in stock data are
        addressed to a semantic their shader does not have -- ``mat_main_2``
        has no ``Bumpiness`` and no ``Roughness``, which fits, since it has no
        normal map either. Editing one of those changes the file and nothing
        else, and this project's whole complaint about silent no-ops
        (``PRJ001``, ``PRJ005``) applies to it.
        """
        self._clear(self.params_form)
        self._param_boxes = {}
        known = set(semantics) if semantics is not None else None
        for name, value in parameters.items():
            spin = QDoubleSpinBox()
            spin.setDecimals(6)
            spin.setRange(-10000.0, 10000.0)
            spin.setSingleStep(0.05)
            if value is None:
                # A <Float> with no value attribute is legal and means "shader
                # default". Showing 0 would be a lie, so it is spelled out and
                # left out of the edit unless touched.
                spin.setSpecialValueText("(shader default)")
                spin.setValue(spin.minimum())
            else:
                spin.setValue(value)
            spin.valueChanged.connect(self._changed)
            self._param_boxes[name] = spin

            label = QLabel(name)
            if known is not None and name not in known:
                # Still editable -- it is in the file and removing the control
                # would just hide the problem -- but plainly marked.
                label.setText(f"{name}  ⚠")
                label.setStyleSheet("color: #9a6f09;")
                tip = (
                    f"This shader does not declare '{name}', so the engine "
                    "never reads it. Editing it changes the scene file and "
                    "nothing on screen."
                )
                label.setToolTip(tip)
                spin.setToolTip(tip)
            self.params_form.addRow(label, spin)

    def _build_material(self, values: List[float]) -> None:
        self._clear(self.material_grid)
        self._material_boxes = []
        if len(values) != 17:
            return
        for r, label in enumerate(MATERIAL_ROWS):
            self.material_grid.addWidget(QLabel(label), r, 0)
            for c in range(4):
                spin = QDoubleSpinBox()
                spin.setDecimals(6)
                spin.setRange(-10000.0, 10000.0)
                spin.setSingleStep(0.05)
                spin.setValue(values[r * 4 + c])
                spin.valueChanged.connect(self._changed)
                self.material_grid.addWidget(spin, r, c + 1)
                self._material_boxes.append(spin)
        self.material_grid.addWidget(QLabel("power"), 4, 0)
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-10000.0, 10000.0)
        spin.setValue(values[16])
        spin.valueChanged.connect(self._changed)
        self.material_grid.addWidget(spin, 4, 1)
        self._material_boxes.append(spin)

    # -- applying ------------------------------------------------------------

    def _reset_to_defaults(self) -> None:
        """Put every value back to the shader's own compiled-in default.

        Not the same as **Revert**, which restores what the scene currently
        says.  This restores what the *shader* says, which is only knowable
        because `.bsd9` is decoded -- and a parameter the shader does not
        declare has no default to restore, so it is left alone rather than
        zeroed.
        """
        if self._loaded is None:
            return
        material_default = self._loaded.get("material_default")
        # The rules live in `session.effect_default_values`, where they can be
        # tested; this only pushes the answer into the boxes.
        wanted = effect_default_values(
            self._loaded.get("parameters") or {}, self._loaded.get("defaults")
        )

        self._loading = True
        try:
            for name, spin in self._param_boxes.items():
                value = wanted.get(name)
                spin.setValue(spin.minimum() if value is None else float(value))
            if material_default and len(material_default) == len(self._material_boxes):
                for spin, value in zip(self._material_boxes, material_default):
                    spin.setValue(float(value))
        finally:
            self._loading = False
        self._changed()

    def _revert(self) -> None:
        self.load(self._loaded)
        self._changed()

    def _apply(self) -> None:
        if self._loaded is None:
            return
        shader = self.shader_edit.text().strip() or None
        if shader == (self._loaded.get("shader") or ""):
            shader = None

        parameters = effect_parameter_edits(
            self._loaded.get("parameters") or {}, self.current_parameters()
        )

        material = None
        if self._material_boxes:
            values = [s.value() for s in self._material_boxes]
            if list(self._loaded.get("material") or ()) != values:
                material = values

        textures = dict(self._texture_edits) or None

        if shader is None and not parameters and material is None and not textures:
            return
        self._on_apply(shader, parameters, material, textures)


class EffectDialog(QDialog):
    """The effect editor as a modal, titled with the submesh it belongs to.

    A dialog rather than a panel for two reasons the panel got wrong: it is
    eleven rows of spin boxes that squeezed the 3D viewport into a strip, and
    sitting below the table it never made clear *which* submesh it was editing.
    A title bar saying ``main_[1]`` does.
    """

    def __init__(self, parent, submesh: str, effect: dict,
                 *, on_preview: Optional[Callable[..., None]] = None,
                 on_pick_texture: Optional[Callable[[int], Optional[str]]] = None
                 ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Shader options — {submesh}")
        self.setMinimumWidth(760)
        self.result_payload: Optional[tuple] = None

        layout = QVBoxLayout(self)
        self.editor = EffectEditor(self._capture, embedded=True,
                                   on_preview=on_preview,
                                   on_pick_texture=on_pick_texture)
        self.editor.load(effect)
        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Apply to mod")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _capture(self, shader, parameters, material, textures=None) -> None:
        self.result_payload = (shader, parameters, material, textures)

    def _accept(self) -> None:
        self.result_payload = None
        self.editor._apply()
        if self.result_payload is None:
            self.reject()            # nothing changed; do not write a no-op
        else:
            self.accept()


__all__ = ["EffectEditor", "EffectDialog", "MATERIAL_ROWS"]
