"""
Message dialog: a title and lines of text, wrapped to fit, closed with OK.
"""

import pygame
from pygame import Rect, Surface

from editor.core.constants import (
    COLOR_BUTTON,
    COLOR_BUTTON_HOVER,
    COLOR_GRID,
    COLOR_PICKER_BG,
    COLOR_TEXT,
)

#: the keys that close the dialog
_CLOSE_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_SPACE)


def wrap(text: str, font: pygame.font.Font, width: int) -> list[str]:
    """`text` broken at spaces into lines no wider than `width`, where it can be."""
    lines: list[str] = []
    for word in text.split(" "):
        if lines and font.size(f"{lines[-1]} {word}")[0] <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


class MessageDialog:
    """Modal dialog drawn over the editor that shows a message until it is closed."""

    WIDTH = 560
    PADDING = 20
    BUTTON_WIDTH = 120
    BUTTON_HEIGHT = 34

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        title: str,
        paragraphs: list[str],
        font: pygame.font.Font,
    ):
        self.font = font
        self.title = title
        width = min(self.WIDTH, screen_width - 40)
        self.lines = [
            line
            for paragraph in paragraphs
            for line in wrap(paragraph, font, width - 2 * self.PADDING)
        ]
        line_height = font.get_linesize()
        height = min(
            screen_height - 40,
            self.PADDING * 4 + line_height * (len(self.lines) + 2) + self.BUTTON_HEIGHT,
        )
        self.rect = Rect(0, 0, width, height)
        self.rect.center = (screen_width // 2, screen_height // 2)
        self.button = Rect(0, 0, self.BUTTON_WIDTH, self.BUTTON_HEIGHT)
        self.button.midbottom = (self.rect.centerx, self.rect.bottom - self.PADDING)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Whether an event closes the dialog."""
        if event.type == pygame.QUIT:
            return True
        if event.type == pygame.KEYDOWN:
            return event.key in _CLOSE_KEYS
        return (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.button.collidepoint(event.pos)
        )

    def render(self, screen: Surface):
        pygame.draw.rect(screen, COLOR_PICKER_BG, self.rect)
        pygame.draw.rect(screen, COLOR_GRID, self.rect, 2)

        line_height = self.font.get_linesize()
        y = self.rect.y + self.PADDING
        title = self.font.render(self.title, True, COLOR_TEXT)
        screen.blit(title, title.get_rect(centerx=self.rect.centerx, y=y))
        y += line_height * 2
        for line in self.lines:
            # lines past the bottom are cut off rather than drawn over the button
            if y + line_height > self.button.top - self.PADDING // 2:
                break
            text = self.font.render(line, True, COLOR_TEXT)
            screen.blit(text, (self.rect.x + self.PADDING, y))
            y += line_height

        hovered = self.button.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(
            screen, COLOR_BUTTON_HOVER if hovered else COLOR_BUTTON, self.button
        )
        pygame.draw.rect(screen, COLOR_GRID, self.button, 1)
        label = self.font.render("OK", True, COLOR_TEXT)
        screen.blit(label, label.get_rect(center=self.button.center))

    def show(self, screen: Surface, clock: pygame.time.Clock) -> None:
        """Run the dialog's own event loop until it is closed."""
        background = screen.copy()
        while True:
            for event in pygame.event.get():
                if self.handle_event(event):
                    return
            screen.blit(background, (0, 0))
            self.render(screen)
            pygame.display.flip()
            clock.tick(60)
