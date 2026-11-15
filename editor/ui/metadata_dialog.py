"""
Metadata editing dialog for a hole's par, distance and handicap.

Allows user to:
- Edit par (3-5), distance (10-999 yards) and handicap (1-18)
- Live validation with visual feedback
- Save or cancel changes
- View putting surface size comparison chart
"""

import json
from dataclasses import dataclass

import pygame
from pygame import Rect, Surface

from editor.core.constants import (
    COLOR_BUTTON,
    COLOR_BUTTON_HOVER,
    COLOR_GRID,
    COLOR_PICKER_BG,
    COLOR_SELECTION,
    COLOR_TEXT,
)
from editor.resources import get_resource_path
from golf.formats.hole_data import HOLE_PARS, HoleData
from golf.formats.putting_surface import count_putting_surface_tiles


def _load_vanilla_sizes() -> list[int]:
    """Load pre-computed putting surface sizes from data file."""
    data_path = get_resource_path("data/statistics/putting_surface_sizes.json")
    if data_path.exists():
        with open(data_path) as f:
            data = json.load(f)
            return data.get("sizes", [])
    return []


@dataclass
class _Field:
    """One of the dialog's number fields: the metadata key it edits and its text."""

    key: str
    label: str
    low: int
    high: int
    digits: int
    hint: str
    text: str = ""
    rect: Rect | None = None

    @property
    def valid(self) -> bool:
        return self.text.isdigit() and self.low <= int(self.text) <= self.high


def _fields() -> list[_Field]:
    # The game always stores a distance as three digits, but a leading zero is fine
    return [
        _Field(
            "par",
            "Par:",
            min(HOLE_PARS),
            max(HOLE_PARS),
            1,
            f"({min(HOLE_PARS)}-{max(HOLE_PARS)})",
        ),
        _Field("distance", "Distance:", 10, 999, 3, "(10-999 yards)"),
        _Field("handicap", "Handicap:", 1, 18, 2, "(1-18, 1 hardest)"),
    ]


class MetadataDialog:
    """Modal dialog for editing hole metadata (par, distance and handicap)."""

    # Validation error color
    COLOR_ERROR = (255, 50, 50)
    # Chart colors
    COLOR_VANILLA_DOT = (128, 128, 128)  # Gray for vanilla holes
    COLOR_CURRENT_DOT = (255, 230, 50)  # Yellow for current hole

    MARGIN = 20
    FIELD_SPACING = 50
    INPUT_HEIGHT = 30
    LABEL_WIDTH = 90

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        hole_data: HoleData,
        font: pygame.font.Font,
    ):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.hole_data = hole_data
        self.font = font

        self.fields = _fields()
        for field in self.fields:
            field.text = str(hole_data.metadata.get(field.key, field.low))
        #: the field typing goes to, or None
        self.active: _Field | None = None

        self.dialog_width = 400
        self.dialog_height = 240 + self.FIELD_SPACING * len(self.fields)

        # Load vanilla sizes and calculate current hole's size
        self.vanilla_sizes = _load_vanilla_sizes()
        self.current_size = count_putting_surface_tiles(hole_data.greens)
        self.dialog_rect = Rect(
            (screen_width - self.dialog_width) // 2,
            (screen_height - self.dialog_height) // 2,
            self.dialog_width,
            self.dialog_height,
        )
        self._calculate_layout()

        # Result tracking
        self.saved = False
        self.canceled = False

    def field(self, key: str) -> _Field:
        """The field that edits metadata `key`."""
        return next(field for field in self.fields if field.key == key)

    def _calculate_layout(self):
        """Calculate layout rectangles."""
        margin = self.MARGIN
        self.title_y = self.dialog_rect.y + margin

        y = self.title_y + 60
        for field in self.fields:
            field.rect = Rect(
                self.dialog_rect.x + margin + self.LABEL_WIDTH + 10,
                y,
                80,
                self.INPUT_HEIGHT,
            )
            y += self.FIELD_SPACING

        # Strip chart area (between the fields and buttons)
        self.chart_rect = Rect(
            self.dialog_rect.x + margin,
            y,
            self.dialog_width - 2 * margin,
            75,  # Chart height including labels
        )

        button_width, button_height = 100, 40
        button_y = self.dialog_rect.bottom - margin - button_height
        self.save_button_rect = Rect(
            self.dialog_rect.centerx - button_width - 10,
            button_y,
            button_width,
            button_height,
        )
        self.cancel_button_rect = Rect(
            self.dialog_rect.centerx + 10,
            button_y,
            button_width,
            button_height,
        )

    def handle_event(self, event: pygame.event.Event) -> bool:
        """
        Handle input events.

        Returns:
            True if dialog should close, False otherwise
        """
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.save_button_rect.collidepoint(event.pos):
                return self._save()
            if self.cancel_button_rect.collidepoint(event.pos):
                self.canceled = True
                return True
            self.active = next(
                (
                    field
                    for field in self.fields
                    if field.rect and field.rect.collidepoint(event.pos)
                ),
                None,
            )

        elif event.type == pygame.KEYDOWN:
            if self.active is not None:
                field = self.active
                if event.key == pygame.K_BACKSPACE:
                    field.text = field.text[:-1]
                elif event.key == pygame.K_TAB:
                    index = self.fields.index(field) + 1
                    self.active = (
                        self.fields[index] if index < len(self.fields) else None
                    )
                elif event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
                    self.active = None
                elif event.unicode.isdigit() and len(field.text) < field.digits:
                    field.text += event.unicode
            elif event.key == pygame.K_ESCAPE:
                self.canceled = True
                return True
            elif event.key == pygame.K_RETURN:
                return self._save()
            elif event.key == pygame.K_TAB:
                self.active = self.fields[0]

        return False

    def _save(self) -> bool:
        """Write the fields to the hole; True if they were all valid (the dialog
        closes), False if one is not (it stays open)."""
        if not all(field.valid for field in self.fields):
            return False
        metadata = self.hole_data.metadata
        values = {field.key: int(field.text) for field in self.fields}
        if any(metadata.get(key) != value for key, value in values.items()):
            metadata.update(values)
            self.hole_data.modified = True
        self.saved = True
        return True

    def summary(self) -> str:
        """The fields as the status bar reports them: `Par=4, Distance=400, ...`."""
        return ", ".join(
            f"{field.label.rstrip(':')}={field.text}" for field in self.fields
        )

    def render(self, screen: Surface):
        """Render the dialog."""
        # Semi-transparent overlay
        overlay = Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill((0, 0, 0))
        screen.blit(overlay, (0, 0))

        pygame.draw.rect(screen, COLOR_PICKER_BG, self.dialog_rect)
        pygame.draw.rect(screen, COLOR_GRID, self.dialog_rect, 2)

        title_surf = self.font.render("Edit Hole Metadata", True, COLOR_TEXT)
        title_rect = title_surf.get_rect(
            centerx=self.dialog_rect.centerx,
            y=self.title_y,
        )
        screen.blit(title_surf, title_rect)

        for field in self.fields:
            self._render_field(screen, field)

        self._render_strip_chart(screen)

        self._render_button(screen, self.save_button_rect, "Save")
        self._render_button(screen, self.cancel_button_rect, "Cancel")

    def _render_field(self, screen: Surface, field: _Field):
        """Render a text input field with its label and hint."""
        rect = field.rect
        assert rect is not None
        label_surf = self.font.render(field.label, True, COLOR_TEXT)
        screen.blit(
            label_surf,
            label_surf.get_rect(
                left=self.dialog_rect.x + self.MARGIN, centery=rect.centery
            ),
        )

        pygame.draw.rect(screen, COLOR_BUTTON, rect)
        if not field.valid:
            border_color = self.COLOR_ERROR
        elif field is self.active:
            border_color = COLOR_SELECTION
        else:
            border_color = COLOR_GRID
        pygame.draw.rect(screen, border_color, rect, 2)

        value_surf = self.font.render(field.text, True, COLOR_TEXT)
        screen.blit(
            value_surf, value_surf.get_rect(left=rect.left + 5, centery=rect.centery)
        )

        hint_surf = self.font.render(field.hint, True, COLOR_GRID)
        screen.blit(
            hint_surf, hint_surf.get_rect(left=rect.right + 10, centery=rect.centery)
        )

    def _render_button(self, screen: Surface, rect: Rect, text: str):
        """Render a button."""
        is_hovered = rect.collidepoint(pygame.mouse.get_pos())
        color = COLOR_BUTTON_HOVER if is_hovered else COLOR_BUTTON

        pygame.draw.rect(screen, color, rect)
        pygame.draw.rect(screen, COLOR_GRID, rect, 1)

        text_surf = self.font.render(text, True, COLOR_TEXT)
        text_rect = text_surf.get_rect(center=rect.center)
        screen.blit(text_surf, text_rect)

    def _render_strip_chart(self, screen: Surface):
        """Render strip chart comparing current putting surface size to vanilla holes."""
        if not self.vanilla_sizes:
            return  # No data to display

        # Calculate bounds
        min_size = min(self.vanilla_sizes)
        max_size = max(self.vanilla_sizes)
        range_size = max_size - min_size
        if range_size == 0:
            range_size = 1  # Avoid division by zero

        # Chart dimensions
        chart_left = self.chart_rect.x + 30  # Leave room for min label
        chart_right = self.chart_rect.right - 30  # Leave room for max label
        chart_width = chart_right - chart_left
        chart_center_y = self.chart_rect.y + 40  # Center line for dots (below title)
        dot_radius = 3  # Vanilla dots
        current_dot_radius = 5  # Current hole dot

        # Title label
        title_surf = self.font.render("Putting Surface Size", True, COLOR_TEXT)
        title_rect = title_surf.get_rect(
            centerx=self.chart_rect.centerx,
            y=self.chart_rect.y,
        )
        screen.blit(title_surf, title_rect)

        # Draw axis line
        pygame.draw.line(
            screen,
            COLOR_GRID,
            (chart_left, chart_center_y),
            (chart_right, chart_center_y),
            1,
        )

        # Draw min/max labels
        min_label = self.font.render(str(min_size), True, COLOR_GRID)
        min_rect = min_label.get_rect(
            right=chart_left - 5,
            centery=chart_center_y,
        )
        screen.blit(min_label, min_rect)

        max_label = self.font.render(str(max_size), True, COLOR_GRID)
        max_rect = max_label.get_rect(
            left=chart_right + 5,
            centery=chart_center_y,
        )
        screen.blit(max_label, max_rect)

        # Compute jitter positions for vanilla dots
        jitter_positions = self._compute_jitter_positions(
            self.vanilla_sizes, chart_left, chart_width, range_size, min_size
        )

        # Draw vanilla dots with jitter
        for x, y_offset in jitter_positions:
            pygame.draw.circle(
                screen,
                self.COLOR_VANILLA_DOT,
                (int(x), int(chart_center_y + y_offset)),
                dot_radius,
            )

        # Calculate current hole position
        current_x = (
            chart_left + (self.current_size - min_size) / range_size * chart_width
        )
        # Clamp to chart bounds
        current_x = max(chart_left, min(chart_right, current_x))

        # Draw current hole dot (larger, yellow)
        pygame.draw.circle(
            screen,
            self.COLOR_CURRENT_DOT,
            (int(current_x), chart_center_y),
            current_dot_radius,
        )
        # Add outline for visibility
        pygame.draw.circle(
            screen,
            COLOR_GRID,
            (int(current_x), chart_center_y),
            current_dot_radius,
            1,
        )

        # Calculate percentile
        below_count = sum(1 for s in self.vanilla_sizes if s < self.current_size)
        percentile = int(below_count / len(self.vanilla_sizes) * 100)

        # Draw percentile text below chart
        percentile_text = f"{self.current_size} tiles ({percentile}th percentile)"
        percentile_surf = self.font.render(percentile_text, True, COLOR_TEXT)
        percentile_rect = percentile_surf.get_rect(
            centerx=self.chart_rect.centerx,
            top=chart_center_y + 15,
        )
        screen.blit(percentile_surf, percentile_rect)

    def _compute_jitter_positions(
        self,
        sizes: list[int],
        chart_left: float,
        chart_width: float,
        range_size: float,
        min_size: int,
    ) -> list[tuple[float, float]]:
        """
        Compute (x, y_offset) positions with vertical jitter for overlapping values.

        Args:
            sizes: List of putting surface sizes
            chart_left: X coordinate of chart left edge
            chart_width: Width of chart area
            range_size: Size range (max - min)
            min_size: Minimum size value

        Returns:
            List of (x, y_offset) tuples
        """
        # Group sizes by their x position (with some tolerance)
        tolerance = chart_width / 50  # Jitter when within ~2% of chart width
        positions = []

        # Calculate x positions first
        x_positions = []
        for size in sizes:
            x = chart_left + (size - min_size) / range_size * chart_width
            x_positions.append(x)

        # Sort indices by x position for grouping
        sorted_indices = sorted(range(len(x_positions)), key=lambda i: x_positions[i])

        # Group nearby points and apply vertical jitter
        jitter_spacing = 5  # Pixels between stacked dots
        i = 0
        while i < len(sorted_indices):
            # Find all points within tolerance of current point
            group_start = i
            base_x = x_positions[sorted_indices[i]]

            while (
                i < len(sorted_indices)
                and x_positions[sorted_indices[i]] - base_x < tolerance
            ):
                i += 1

            group_size = i - group_start

            # Apply vertical jitter to group (centered on axis)
            for j in range(group_size):
                idx = sorted_indices[group_start + j]
                offset = (j - (group_size - 1) / 2) * jitter_spacing
                positions.append((x_positions[idx], offset))

        return positions
