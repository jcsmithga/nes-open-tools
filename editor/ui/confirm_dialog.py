"""
Unsaved changes dialog: asks whether to save the hole before it is closed or replaced.
"""

from enum import Enum

import pygame
from pygame import Rect, Surface

from editor.core.constants import (
    COLOR_BUTTON,
    COLOR_BUTTON_HOVER,
    COLOR_GRID,
    COLOR_PICKER_BG,
    COLOR_TEXT,
)


class UnsavedChoice(Enum):
    """What to do with unsaved changes."""

    SAVE = "Save"
    DISCARD = "Don't Save"
    CANCEL = "Cancel"


#: the keys that answer the dialog without the mouse
_KEY_CHOICES = {
    pygame.K_RETURN: UnsavedChoice.SAVE,
    pygame.K_KP_ENTER: UnsavedChoice.SAVE,
    pygame.K_s: UnsavedChoice.SAVE,
    pygame.K_d: UnsavedChoice.DISCARD,
    pygame.K_n: UnsavedChoice.DISCARD,
    pygame.K_ESCAPE: UnsavedChoice.CANCEL,
}


class UnsavedChangesDialog:
    """Modal Save / Don't Save / Cancel dialog drawn over the editor."""

    WIDTH = 460
    HEIGHT = 150
    BUTTON_WIDTH = 120
    BUTTON_HEIGHT = 34

    def __init__(
        self, screen_width: int, screen_height: int, name: str, font: pygame.font.Font
    ):
        self.font = font
        self.message = f"Save changes to {name}?"
        self.rect = Rect(0, 0, self.WIDTH, self.HEIGHT)
        self.rect.center = (screen_width // 2, screen_height // 2)

        gap = (self.WIDTH - 3 * self.BUTTON_WIDTH) // 4
        top = self.rect.bottom - self.BUTTON_HEIGHT - 20
        self.buttons = [
            (
                choice,
                Rect(
                    self.rect.x + gap + i * (self.BUTTON_WIDTH + gap),
                    top,
                    self.BUTTON_WIDTH,
                    self.BUTTON_HEIGHT,
                ),
            )
            for i, choice in enumerate(UnsavedChoice)
        ]

    def handle_event(self, event: pygame.event.Event) -> UnsavedChoice | None:
        """The choice an event makes, or None if it makes none."""
        if event.type == pygame.QUIT:
            return UnsavedChoice.CANCEL
        if event.type == pygame.KEYDOWN:
            return _KEY_CHOICES.get(event.key)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for choice, rect in self.buttons:
                if rect.collidepoint(event.pos):
                    return choice
        return None

    def render(self, screen: Surface):
        pygame.draw.rect(screen, COLOR_PICKER_BG, self.rect)
        pygame.draw.rect(screen, COLOR_GRID, self.rect, 2)

        text = self.font.render(self.message, True, COLOR_TEXT)
        screen.blit(text, text.get_rect(centerx=self.rect.centerx, y=self.rect.y + 30))

        mouse = pygame.mouse.get_pos()
        for choice, rect in self.buttons:
            color = COLOR_BUTTON_HOVER if rect.collidepoint(mouse) else COLOR_BUTTON
            pygame.draw.rect(screen, color, rect)
            pygame.draw.rect(screen, COLOR_GRID, rect, 1)
            label = self.font.render(choice.value, True, COLOR_TEXT)
            screen.blit(label, label.get_rect(center=rect.center))

    def show(self, screen: Surface, clock: pygame.time.Clock) -> UnsavedChoice:
        """Run the dialog's own event loop until a choice is made."""
        background = screen.copy()
        while True:
            for event in pygame.event.get():
                choice = self.handle_event(event)
                if choice is not None:
                    return choice
            screen.blit(background, (0, 0))
            self.render(screen)
            pygame.display.flip()
            clock.tick(60)
