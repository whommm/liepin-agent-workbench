"""Application icon generator for the liepin workbench."""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QLinearGradient, QFont, QPen


def create_app_icon() -> QIcon:
    """Create a modern application icon programmatically.

    Returns a QIcon with multiple sizes for window title bar and taskbar.
    """
    icon = QIcon()

    # Generate multiple sizes for different contexts
    for size in [16, 24, 32, 48, 64, 128, 256]:
        pixmap = _render_icon(size)
        icon.addPixmap(pixmap)

    return icon


def _render_icon(size: int) -> QPixmap:
    """Render the icon at a specific size."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # Background gradient - warm amber/gold
    margin = size * 0.08
    rect_size = size - margin * 2

    gradient = QLinearGradient(margin, margin, size - margin, size - margin)
    gradient.setColorAt(0, QColor("#e8a855"))  # Warm gold
    gradient.setColorAt(0.5, QColor("#d4944a"))  # Amber
    gradient.setColorAt(1, QColor("#c4854a"))  # Deep amber

    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)

    # Rounded rectangle background
    corner_radius = size * 0.22
    painter.drawRoundedRect(
        int(margin), int(margin),
        int(rect_size), int(rect_size),
        corner_radius, corner_radius
    )

    # Draw stylized "L" letter (for Liepin) or abstract search icon
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 230))

    # Inner content - abstract "agent" symbol (person + magnifier)
    center_x = size / 2
    center_y = size / 2

    # Draw a stylized person silhouette (upper body)
    head_radius = size * 0.12
    painter.drawEllipse(
        int(center_x - head_radius),
        int(center_y - size * 0.22),
        int(head_radius * 2),
        int(head_radius * 2)
    )

    # Body arc
    body_width = size * 0.32
    body_height = size * 0.18
    painter.drawEllipse(
        int(center_x - body_width / 2),
        int(center_y + size * 0.02),
        int(body_width),
        int(body_height)
    )

    # Magnifying glass overlay (bottom right)
    mag_x = center_x + size * 0.12
    mag_y = center_y + size * 0.08
    mag_radius = size * 0.14

    # Glass circle
    pen = QPen(QColor(255, 255, 255, 200))
    pen.setWidth(max(2, int(size * 0.06)))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(
        int(mag_x - mag_radius),
        int(mag_y - mag_radius),
        int(mag_radius * 2),
        int(mag_radius * 2)
    )

    # Handle
    handle_start_x = mag_x + mag_radius * 0.7
    handle_start_y = mag_y + mag_radius * 0.7
    handle_length = size * 0.12
    painter.drawLine(
        int(handle_start_x), int(handle_start_y),
        int(handle_start_x + handle_length), int(handle_start_y + handle_length)
    )

    painter.end()
    return pixmap
