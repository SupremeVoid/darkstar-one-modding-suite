"""
The Problems tab: the diagnostics engine, rendered.

Grouped by rule rather than by file, because the useful question is "what kind
of thing is wrong" before "where".  Errors sort first, and every row carries the
fix when the library knows one.

Four rules can also be *repaired* from here.  Which four, and why not the rest,
is decided in :attr:`Session.FIXES` -- this file only renders the offer.  The
tab does no repairing itself: it asks the session, shows what will happen,
and re-runs the validation afterwards.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.validate import Severity

from .. import theme

_COLOUR = {
    Severity.ERROR: theme.SEVERITY["error"],
    Severity.WARNING: theme.SEVERITY["warning"],
    Severity.INFO: theme.SEVERITY["info"],
    Severity.HINT: theme.SEVERITY["hint"],
}


class ProblemsTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.headline = QLabel("Not validated yet.")
        self.show_info = QCheckBox("Show info and hints")
        self.show_info.setChecked(True)
        self.show_info.stateChanged.connect(self.refresh)
        self.btn_fix = QPushButton("Fix")
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(self.fix_selected)
        btn = QPushButton("Validate")
        btn.clicked.connect(window.run_validation)
        row.addWidget(self.headline, 1)
        row.addWidget(self.show_info)
        row.addWidget(self.btn_fix)
        row.addWidget(btn)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Problem", "Where", "Fix"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(lambda *_: self._sync_fix())
        self.tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.tree, 1)

        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        report = self.session.report
        if report is None:
            self.headline.setText("Not validated yet.")
            self.btn_fix.setVisible(False)
            return

        counts = report.counts()
        headline = (
            (f"<b style='color:{theme.RED}'>Not deployable</b> — "
             if not report.ok else "")
            + (", ".join(f"{v} {k}" for k, v in counts.items()) or "no findings")
        )
        if report.skipped:
            # "No findings" from a run that could not check everything is a lie
            # of omission, and the more reassuring it looks the worse it is.
            headline += (
                f" &nbsp;·&nbsp; <span style='color:{theme.AMBER}'>"
                f"{len(report.skipped)} rule group(s) not checked</span>"
            )
        self.headline.setText(headline)
        self.headline.setToolTip(
            "\n".join(f"{rule}: {why}" for rule, why in report.skipped.items())
        )

        allowed = {Severity.ERROR, Severity.WARNING}
        if self.show_info.isChecked():
            allowed |= {Severity.INFO, Severity.HINT}

        for code, group in sorted(
            report.by_code().items(),
            key=lambda kv: (Severity.rank(kv[1][0].severity), kv[0]),
        ):
            sev = group[0].severity
            if sev not in allowed:
                continue
            parent = QTreeWidgetItem([f"{code}  {group[0].message}", f"{len(group)}", ""])
            parent.setData(0, Qt.ItemDataRole.UserRole, code)
            brush = QBrush(QColor(_COLOUR.get(sev, "#000000")))
            parent.setForeground(0, brush)
            if group[0].detail:
                parent.setToolTip(0, group[0].detail)
            for d in group[:500]:
                child = QTreeWidgetItem(
                    [d.message, f"{d.path or ''}{f'  ({d.location})' if d.location else ''}", d.fix or ""]
                )
                if d.detail:
                    child.setToolTip(0, d.detail)
                child.setData(0, Qt.ItemDataRole.UserRole, code)
                parent.addChild(child)
            dropped = report.truncated().get(code, 0)
            if dropped:
                # Say so. A silent cut reads as "that is all there is".
                parent.setText(1, f"{report.totals.get(code, len(group))}")
                parent.addChild(
                    QTreeWidgetItem(
                        [f"… and {dropped} more not listed", "", ""]
                    )
                )
            self.tree.addTopLevelItem(parent)

        for rule, why in sorted(report.skipped.items()):
            item = QTreeWidgetItem([f"NOT CHECKED  {rule}", "", why])
            item.setForeground(0, QBrush(QColor(theme.AMBER)))
            item.setToolTip(0, why)
            self.tree.addTopLevelItem(item)

        self.tree.expandToDepth(0)
        self._sync_fix()

    # -- the four mechanical fixes -------------------------------------------

    def selected_code(self):
        """The rule code of the selected row, whether group or child."""
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _sync_fix(self) -> None:
        code = self.selected_code()
        offer = self.session.fix_for(code) if code else None
        self.btn_fix.setVisible(offer is not None)
        if offer:
            self.btn_fix.setText(offer[0])
            self.btn_fix.setToolTip(offer[1])

    def _menu(self, point) -> None:
        code = self.selected_code()
        offer = self.session.fix_for(code) if code else None
        if not offer:
            return
        menu = QMenu(self)
        action = QAction(offer[0], self)
        action.setToolTip(offer[1])
        action.triggered.connect(self.fix_selected)
        menu.addAction(action)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def fix_selected(self) -> None:
        """Repair the whole rule group, after saying what that means.

        The confirmation is not a formality: each of these writes to the mod
        folder, and two of them move files between the archive and the loose
        tree.  What the fix will do is the session's own words, so the dialog
        cannot drift from the behaviour.
        """
        code = self.selected_code()
        offer = self.session.fix_for(code) if code else None
        if not offer:
            return
        label, explanation = offer
        if QMessageBox.question(
            self, label,
            f"<b>{code}</b><br><br>{explanation}<br><br>Go ahead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            summary = self.session.apply_fix(code)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.warning(self, label, str(exc))
            return
        QMessageBox.information(self, label, summary)
        # The repair invalidated the report; re-run rather than leave a list
        # that is right about one row and stale about the rest.
        self.window.run_validation()


__all__ = ["ProblemsTab"]
