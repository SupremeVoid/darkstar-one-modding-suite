"""
One documented API symbol, rendered for a person to read.

Kept out of the tab, and free of Qt, for the same reason as ``theme.py``: this
is the part with rules worth testing -- an optional parameter must not be
presented as required, and an inserted call skeleton must not pre-fill six
optional table keys the author then has to delete.
"""

from __future__ import annotations


def call_skeleton(symbol: dict) -> str:
    """``NComm.AddMessage( { Text=, Voice= } )`` -- the required fields only.

    Optional parameters are left out on purpose: the point of inserting a
    skeleton is to get the shape right, and a table pre-filled with six
    optional keys is something the author has to delete.
    """
    required = [p["name"] for p in symbol.get("parameters", ())
                if not p.get("optional")]
    qualified = symbol.get("qualified") or symbol["name"]
    if symbol.get("kind") == "event":
        return f'"{symbol["name"]}"'
    if not symbol.get("parameters"):
        return f"{qualified}()"
    if "{" not in symbol.get("signature", ""):
        return f"{qualified}({', '.join(required)})"
    fields = ", ".join(f"{name}=" for name in required)
    return f"{qualified}( {{ {fields} }} )"


def symbol_html(symbol: dict) -> str:
    """One documented symbol, as the reference pane shows it."""
    def escape(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    parts = [f"<h3 style='margin-bottom:2px'>{escape(symbol['qualified'])}</h3>",
             f"<code>{escape(symbol['signature'])}</code>"]
    if symbol.get("summary"):
        parts.append(f"<p>{escape(symbol['summary'])}</p>")
    if symbol.get("trigger"):
        parts.append(f"<p><b>Triggered by:</b> {escape(symbol['trigger'])}</p>")

    for label, rows in (("Parameters", symbol.get("parameters")),
                        ("Returns", symbol.get("returns"))):
        if not rows:
            continue
        parts.append(f"<p><b>{label}</b></p><table cellspacing='0' cellpadding='3'>")
        for row in rows:
            name = escape(row["name"])
            if row.get("optional"):
                name = f"<i>{name}</i> <small>(optional)</small>"
            parts.append(
                f"<tr><td valign='top'><b>{name}</b></td>"
                f"<td valign='top'><code>{escape(row.get('type', ''))}</code></td>"
                f"<td valign='top'>{escape(row.get('comment', ''))}</td></tr>")
        parts.append("</table>")

    if symbol.get("example"):
        parts.append(f"<p><b>Example</b></p><pre>{escape(symbol['example'])}</pre>")
    return "".join(parts)

__all__ = ["call_skeleton", "symbol_html"]
