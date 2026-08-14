"""
Audio: what the game and the mod declare, and what it sounds like.

The engine does not scan folders for sounds -- it reads a database, and a file
nobody declared is simply not there. So this tab is a view of the *declaration*
with the file as a property of it, not a file browser. The game's 442 sounds
and the mod's own are shown together, because "what already exists and which
ones are mine" is one question.

Adding a sound copies the file into the mod and writes the entry, filling in
``Channels``, ``Freq`` and ``Duration`` from the file's own headers. Those are
not decoration: the engine reads them from the database rather than from the
file, so a wrong ``Duration`` truncates playback and looks like a corrupt file.

Playback is Qt's, and deliberately kept to this layer -- the library has no
business depending on a media stack. Nothing here parses or writes anything;
:mod:`dso_app.session` does that.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.errors import DsoError

from ..transport_view import clock as _clock
from ..transport_view import seek_value

#: A declared sound whose file is not there.
_MISSING = "#c0392b"
#: This mod's own, as opposed to the game's.
_MINE = "#1f7a4d"
#: A mod entry answering to the same address as a stock one.
_SHADOW = "#9a6f09"

COLUMNS = ("Sound", "Kind", "Length", "Rate", "From", "File")


class SeekSlider(QSlider):
    """A slider that jumps where you click, instead of paging towards it.

    Qt's default is a page step, which is right for a scrollbar and wrong for a
    scrubber: clicking two thirds along a track should go two thirds in, not
    nudge forward by a fixed amount. Implemented here rather than through a
    proxy style because it is four lines and affects exactly one widget.
    """

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(seek_value(event.position().x(), self.width(),
                                     self.minimum(), self.maximum()))
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.sliderReleased.emit()


def _length(seconds: Optional[float]) -> str:
    if not seconds:
        return ""
    if seconds < 60:
        return f"{seconds:.2f}s"
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


class AudioTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self._rows: List[dict] = []

        layout = QVBoxLayout(self)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status)

        # -- filters ----------------------------------------------------------
        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter by name, group or file…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit, 1)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("every kind", None)
        for kind in ("Stream", "Sound2D", "Sound3D"):
            self.kind_filter.addItem(kind, kind)
        self.kind_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        row.addWidget(self.kind_filter)
        self.source_filter = QComboBox()
        self.source_filter.addItem("mod and game", None)
        self.source_filter.addItem("this mod only", "mod")
        self.source_filter.addItem("the game only", "game")
        self.source_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        row.addWidget(self.source_filter)
        # Adding a sound is the one action here that acts on the *list* rather
        # than on the selected row, so it sits with the filters instead of
        # among Override/Replace/Remove, which all need a selection.
        self.btn_add = QPushButton("Add sound…")
        self.btn_add.setToolTip(
            "Copy a WAV or MP3 into this mod and declare it. The engine reads "
            "rate and length from the database, so they are filled in from the "
            "file itself.")
        self.btn_add.clicked.connect(self.add_sound)
        row.addWidget(self.btn_add)
        layout.addLayout(row)

        # -- the tree ---------------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._sync)
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self.play())
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree, 1)

        # -- playback ---------------------------------------------------------
        play_box = QGroupBox("Preview")
        pl = QVBoxLayout(play_box)
        self.now = QLabel("Select a sound.")
        self.now.setWordWrap(True)
        pl.addWidget(self.now)

        transport = QHBoxLayout()
        # Qt's own media icons rather than glyphs: the app ships no icon theme,
        # and U+23F8 (pause) is recent enough that an older system font draws a
        # box instead. These two are built into every Qt style.
        style = self.style()
        self._icon_play = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        self.btn_play = QPushButton(self._icon_play, "")
        self.btn_play.setFixedWidth(44)
        self.btn_play.setToolTip("Play or pause the selected sound")
        self.btn_play.clicked.connect(self.toggle_play)
        transport.addWidget(self.btn_play)

        self.elapsed = QLabel(_clock(0))
        self.elapsed.setMinimumWidth(48)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        transport.addWidget(self.elapsed)

        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setTracking(False)      # only seek when the handle is let go
        self.seek.sliderPressed.connect(self._seek_started)
        self.seek.sliderReleased.connect(self._seek_finished)
        self.seek.sliderMoved.connect(self._seek_preview)
        transport.addWidget(self.seek, 1)

        self.total = QLabel(_clock(0))
        self.total.setMinimumWidth(48)
        transport.addWidget(self.total)

        transport.addSpacing(12)
        transport.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setMaximumWidth(120)
        self.volume.valueChanged.connect(
            lambda v: self.audio_out.setVolume(v / 100.0))
        transport.addWidget(self.volume)
        pl.addLayout(transport)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self.btn_override = QPushButton("Override…")
        self.btn_override.setToolTip(
            "Declare this stock sound's group and name in the mod, against a "
            "file of your own. The mod's declaration wins, so the stock sound "
            "stops being heard — measured in game.")
        self.btn_override.clicked.connect(self.override_sound)
        self.btn_replace = QPushButton("Replace file…")
        self.btn_replace.setToolTip(
            "Point one of this mod's own declarations at a different file.")
        self.btn_replace.clicked.connect(self.replace_sound)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self.remove_sound)
        for b in (self.btn_override, self.btn_replace, self.btn_remove):
            controls.addWidget(b)
        layout.addWidget(play_box)
        layout.addLayout(controls)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.7)
        self.player.setAudioOutput(self.audio_out)
        self.player.errorOccurred.connect(self._on_error)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(lambda _s: self._sync_transport())
        #: True while the handle is held, so the player's own position updates
        #: do not fight the finger dragging it.
        self._scrubbing = False
        #: What is loaded, which is not always what is selected.
        self._loaded: Optional[str] = None

        self.refresh()

    # -- population -----------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list without throwing away where the user was.

        Every write emits, and every emit refreshes every tab, so this runs
        after each edit. Clearing the tree collapsed all 285 groups and dropped
        the selection, which made replacing two files in a row an exercise in
        finding your place again.
        """
        self.stop()
        self._reset_transport(None)
        keep_open, keep_on, scrolled = self._viewpoint()
        self._rows = self.session.sounds()
        self._fill()
        self._restore(keep_open, keep_on, scrolled)
        self._sync()

    def _viewpoint(self):
        """Which groups are open, what is selected, and where the view sits."""
        open_paths = set()

        def walk(item):
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row is None and item.isExpanded():
                open_paths.add(self._group_path(item))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        selected = self.selected()
        return (open_paths,
                selected["reference"] if selected else None,
                self.tree.verticalScrollBar().value())

    @staticmethod
    def _group_path(item) -> str:
        parts = []
        while item is not None and item.data(0, Qt.ItemDataRole.UserRole) is None:
            parts.append(item.text(0))
            item = item.parent()
        return "/".join(reversed(parts))

    def _restore(self, open_paths, reference, scrolled) -> None:
        def walk(item):
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row is None:
                item.setExpanded(self._group_path(item) in open_paths)
            elif reference and row["reference"] == reference:
                # A mod entry and the stock one it shadows share a reference;
                # preferring the editable one keeps the buttons where they were.
                if not self.tree.selectedItems() or row["editable"]:
                    self.tree.setCurrentItem(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        self.tree.verticalScrollBar().setValue(scrolled)

    def _fill(self) -> None:
        self.tree.clear()
        parents: Dict[str, QTreeWidgetItem] = {}
        # A mod entry on the same group and name as a stock one. The mod's
        # wins -- measured in game -- so the row can say so plainly.
        stock_refs = {r["reference"] for r in self._rows if r["where"] == "game"}

        def parent_for(path: str) -> Optional[QTreeWidgetItem]:
            if not path:
                return None
            if path in parents:
                return parents[path]
            head, _, tail = path.rpartition("/")
            node = QTreeWidgetItem([tail or path])
            node.setFirstColumnSpanned(True)
            above = parent_for(head) if head else None
            if above is None:
                self.tree.addTopLevelItem(node)
            else:
                above.addChild(node)
            parents[path] = node
            return node

        for row in self._rows:
            item = QTreeWidgetItem([
                row["name"],
                row["kind"],
                _length(row["seconds"]),
                f"{row['frequency']:,} Hz" if row["frequency"] else "",
                "this mod" if row["where"] == "mod" else "game",
                row["resource"],
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            shadows = row["where"] == "mod" and row["reference"] in stock_refs
            if not row["exists"]:
                item.setForeground(0, QBrush(QColor(_MISSING)))
                item.setText(5, row["resource"] + "   (file not found)")
            elif row["where"] == "mod":
                item.setForeground(4, QBrush(QColor(_MINE)))
            if shadows:
                item.setText(4, "this mod — replaces the game's")
                item.setForeground(4, QBrush(QColor(_SHADOW)))
                item.setToolTip(
                    4, "The game declares this group and name too, and this "
                       "declaration is the one that plays.")
            node = parent_for(row["group"])
            if node is None:
                self.tree.addTopLevelItem(item)
            else:
                node.addChild(item)

        for column in (1, 2, 3, 4):
            self.tree.resizeColumnToContents(column)
        self._describe()

    def _describe(self) -> None:
        mine = [r for r in self._rows if r["where"] == "mod"]
        gone = [r for r in self._rows if not r["exists"]]
        if not self._rows:
            self.status.setText(
                "No sound database in play. Open a game folder to browse the "
                "game's 442 sounds, or add one to this mod — the engine only "
                "plays what a database declares, so a file on its own is "
                "inaudible.")
            return
        bits = [f"<b>{len(self._rows)} declared sound(s)</b>",
                f"{len(mine)} from this mod"]
        if gone:
            bits.append(f"<span style='color:{_MISSING}'>{len(gone)} whose file "
                        f"is missing</span>")
        # A file nothing declares is not reported here on purpose: SND002 says
        # so in Problems, and the Project tab lists it among the mod's files.
        # Replacing a sound already cleans up after itself, so a leftover got
        # in some other way and belongs where every other stray file is shown.
        self.status.setText(" &nbsp;·&nbsp; ".join(bits)
                            + ". A sound is addressed by its group and name; "
                              "the same name in two groups is two sounds.")

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        kind = self.kind_filter.currentData()
        where = self.source_filter.currentData()

        def visit(item: QTreeWidgetItem) -> bool:
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row is None:                       # a group node
                shown = False
                for i in range(item.childCount()):
                    shown = visit(item.child(i)) or shown
                item.setHidden(not shown)
                return shown
            ok = True
            if needle:
                hay = " ".join([row["name"], row["group"], row["resource"]]).lower()
                ok = needle in hay
            if ok and kind:
                ok = row["kind"] == kind
            if ok and where:
                ok = row["where"] == where
            item.setHidden(not ok)
            return ok

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    # -- selection ------------------------------------------------------------

    def selected(self) -> Optional[dict]:
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _sync(self) -> None:
        row = self.selected()
        has_mod = self.session.mod is not None
        stock_row = bool(row and not row["editable"])
        mod_row = bool(row and row["editable"])
        self.btn_add.setEnabled(has_mod)

        # Shown or hidden by which *kind* of row is selected, then enabled or
        # not by what can be done to it. An action that cannot ever apply to
        # this row is absent; one that could but currently cannot is present
        # and greyed, because "already overridden" is worth saying.
        self.btn_override.setVisible(stock_row)
        self.btn_override.setEnabled(has_mod and stock_row
                                     and not self._already_overridden(row))
        self.btn_replace.setVisible(mod_row)
        self.btn_replace.setEnabled(mod_row)
        self.btn_remove.setVisible(mod_row)
        self.btn_remove.setEnabled(mod_row)
        # Selecting something else points the transport at it: a bar that keeps
        # counting through a track you are no longer looking at says nothing.
        if (row["path"] if row else None) != self._loaded:
            self._reset_transport(row)
        else:
            self._sync_transport()
        if row is None:
            self.now.setText("Select a sound.")
            return
        bits = [f"<b>{row['reference']}</b>", row["kind"]]
        if row["channels"]:
            bits.append("mono" if row["channels"] == 1 else f"{row['channels']} ch")
        if row["seconds"]:
            bits.append(_length(row["seconds"]))
        note = "" if row["exists"] else (
            f"<br><span style='color:{_MISSING}'>The file this names is not "
            f"there, so the sound cannot play in game either.</span>")
        if not row["editable"] and self._already_overridden(row):
            note += (f"<br><span style='color:{_SHADOW}'>This mod declares the "
                     f"same group and name, so this stock sound is not "
                     f"heard.</span>")
        self.now.setText(" &nbsp;·&nbsp; ".join(bits)
                         + f"<br><code>{row['path'] or row['resource']}</code>"
                         + note)

    # -- playback -------------------------------------------------------------

    def toggle_play(self) -> None:
        """Play the selected sound, or pause whatever is playing."""
        row = self.selected()
        if not row or not row["exists"]:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        # Paused part-way through this same file: resume rather than restart.
        if (self._loaded == row["path"]
                and self.player.playbackState()
                == QMediaPlayer.PlaybackState.PausedState):
            self.player.play()
            return
        self._loaded = row["path"]
        self.player.setSource(QUrl.fromLocalFile(row["path"]))
        self.player.play()

    def play(self) -> None:
        """Start the selected sound from the beginning."""
        row = self.selected()
        if not row or not row["exists"]:
            return
        self._loaded = row["path"]
        self.player.setSource(QUrl.fromLocalFile(row["path"]))
        self.player.play()

    def stop(self) -> None:
        self.player.stop()
        self._loaded = None

    # -- the transport --------------------------------------------------------

    def _on_position(self, milliseconds: int) -> None:
        if self._scrubbing:
            return
        self.seek.setValue(milliseconds)
        self.elapsed.setText(_clock(milliseconds))

    def _on_duration(self, milliseconds: int) -> None:
        """The decoder's length, which supersedes the declared one.

        They can disagree: the database's ``Duration`` is what the *engine*
        believes, and a mod with a stale value is exactly the case ``SND004``
        reports. Showing the real one while a file plays makes that visible.
        """
        self.seek.setRange(0, max(0, milliseconds))
        self.total.setText(_clock(milliseconds))

    def _seek_started(self) -> None:
        self._scrubbing = True

    def _seek_preview(self, milliseconds: int) -> None:
        self.elapsed.setText(_clock(milliseconds))

    def _seek_finished(self) -> None:
        self._scrubbing = False
        self.player.setPosition(self.seek.value())

    def _sync_transport(self) -> None:
        """Button face and enablement, from the player and the selection."""
        row = self.selected()
        playable = bool(row and row["exists"])
        playing = (self.player.playbackState()
                   == QMediaPlayer.PlaybackState.PlayingState)
        self.btn_play.setIcon(self._icon_pause if playing else self._icon_play)
        self.btn_play.setEnabled(playable)
        self.seek.setEnabled(playable)

    def _reset_transport(self, row: Optional[dict]) -> None:
        """Point the transport at a newly selected sound.

        The length shown before anything plays is the *declared* one, because
        that is all that is known without decoding -- and it is also what the
        engine will act on, so it is the honest thing to show.
        """
        self.stop()
        self.seek.setValue(0)
        self.elapsed.setText(_clock(0))
        declared = int((row["seconds"] or 0) * 1000) if row else 0
        self.seek.setRange(0, declared)
        self.total.setText(_clock(declared))
        self._sync_transport()

    def _on_error(self, _error, message: str) -> None:
        # A codec the platform cannot handle is not the mod's fault, and the
        # engine may well play it. Say so rather than implying the file is bad.
        row = self.selected()
        self.now.setText(
            f"Could not preview {row['name'] if row else 'that'}: {message}. "
            f"This is the preview's limitation, not necessarily the file's.")

    # -- editing --------------------------------------------------------------

    def add_sound(self) -> None:
        if not self.session.mod:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Add a sound", "", "Sound files (*.wav *.mp3)")
        if not path:
            return
        from ..sound_panel import AddSoundDialog

        # Saving emits "mod", which refreshes every tab including this one,
        # so there is nothing to do here on success.
        AddSoundDialog(self.session, path, self).exec()

    def _already_overridden(self, row: dict) -> bool:
        return any(r["where"] == "mod" and r["reference"] == row["reference"]
                   for r in self._rows)

    def override_sound(self) -> None:
        """Give a stock sound a mod declaration pointing at another file.

        Confirmed in game: the mod's declaration is the one that plays, so this
        genuinely displaces the stock sound without touching the installation.
        """
        row = self.selected()
        if not row or row["editable"] or not self.session.mod:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Override {row['reference']}", "",
            "Sound files (*.wav *.mp3)")
        if not path:
            return
        self.stop()
        try:
            self.session.override_sound(row["reference"], path)
        except DsoError as exc:
            QMessageBox.warning(self, "Override", str(exc))

    def replace_sound(self) -> None:
        row = self.selected()
        if not row or not row["editable"]:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Replace the file behind {row['name']}", "",
            "Sound files (*.wav *.mp3)")
        if not path:
            return
        self.stop()
        try:
            self.session.replace_sound_file(row["reference"], path)
        except DsoError as exc:
            QMessageBox.warning(self, "Replace file", str(exc))

    def remove_sound(self) -> None:
        row = self.selected()
        if not row or not row["editable"]:
            return
        answer = QMessageBox.question(
            self, "Remove sound",
            f"Undeclare {row['reference']}?\n\n"
            f"The engine will no longer know about it. Delete the file too?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return
        self.stop()
        try:
            self.session.remove_sound(
                row["reference"],
                delete_file=answer == QMessageBox.StandardButton.Yes)
        except DsoError as exc:
            QMessageBox.warning(self, "Remove sound", str(exc))


__all__ = ["AudioTab"]
