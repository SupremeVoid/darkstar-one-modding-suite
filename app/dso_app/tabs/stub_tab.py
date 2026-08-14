"""
A tab that is not built yet, and says exactly that.

Capability-gating rather than hiding: an empty tab that explains what already
works underneath it is honest about the project's state.  A missing tab implies
the format is not understood, which in most of these cases is untrue -- the
library can already do the work; only the front end is absent.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class StubTab(QWidget):
    def __init__(self, title: str, summary: str, status: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        head = QLabel(f"<h2>{title}</h2>")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel(summary)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("font-size: 14px;")

        body = QLabel(status)
        body.setWordWrap(True)
        body.setMaximumWidth(560)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setStyleSheet("color: palette(mid);")

        for w in (head, sub, body):
            layout.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)


__all__ = ["StubTab"]
