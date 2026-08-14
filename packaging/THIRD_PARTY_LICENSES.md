# Third-party components

The Darkstar One Modding Suite is MIT licensed. It is distributed with the
components below, which keep their own licences.

## PySide6 — LGPL v3

Copyright © The Qt Company Ltd.

PySide6 and the Qt libraries it wraps are licensed under the GNU Lesser General
Public License version 3. The full text is at
<https://www.gnu.org/licenses/lgpl-3.0.html>.

The LGPL constrains the *library*, not an application that links it. This build
satisfies its relinking requirement the way essentially every open-source Python
Qt application does:

* Qt and PySide6 ship as **separate dynamically-linked files** inside this
  folder, not statically linked into the executable;
* they can be replaced with a compatible build of the same version by
  overwriting those files;
* nothing here is modified from the upstream wheels.

Sources for the Qt libraries are available from
<https://download.qt.io/> and for PySide6 from
<https://code.qt.io/pyside/pyside-setup>.

## Pillow — MIT-CMU

Copyright © 1997-2011 by Secret Labs AB, © 1995-2011 by Fredrik Lundh and
contributors. <https://github.com/python-pillow/Pillow/blob/main/LICENSE>

## NumPy — BSD 3-Clause

Copyright © 2005-2025, NumPy Developers.
<https://numpy.org/doc/stable/license.html>

## Python — PSF License Agreement

Copyright © 2001-2025 Python Software Foundation.
<https://docs.python.org/3/license.html>

---

## Not distributed here

The Darkstar One game data this tool reads is **not** redistributed with it and
is not covered by any of the above. Darkstar One is © Ascaron Entertainment.
Nothing in this project contains game assets; it reads the copy you already own.
