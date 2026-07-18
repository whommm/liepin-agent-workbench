"""Shared chat-bubble HTML helpers (QTextBrowser subset).

Used by NewSessionDialog's JD discussion view and the main-window timeline
when rendering user/agent chat messages.
"""

from __future__ import annotations

import html

BUBBLE_STYLES = {
    # (气泡底色, 角色标签色, 角色名)
    "user": ("#f6e9da", "#a96632", "用户"),
    "assistant": ("#f2f4f7", "#475467", "寻访顾问"),
}


def bubble_html(role: str, content: str, label: str = "") -> str:
    """Render one chat message as a left/right aligned bubble table.

    ``label`` overrides the default role name from BUBBLE_STYLES.
    """
    bg, fg, default_label = BUBBLE_STYLES.get(role, BUBBLE_STYLES["assistant"])
    body = html.escape(content).replace("\n", "<br>")
    bubble = (
        '<td width="82%" bgcolor="{bg}">'
        '<div style="font-size:11px; font-weight:600; color:{fg};">{label}</div>'
        '<div style="color:#252a32;">{body}</div>'
        "</td>"
    ).format(bg=bg, fg=fg, label=label or default_label, body=body)
    spacer = '<td width="18%"></td>'
    # 用户气泡靠右，对方气泡靠左
    cells = (spacer + bubble) if role == "user" else (bubble + spacer)
    return (
        '<table width="100%" cellpadding="10" cellspacing="0">'
        "<tr>{}</tr></table>".format(cells)
        + '<div style="font-size:6px;"> </div>'
    )
