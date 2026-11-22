"""The theme made of the terminal's own colours."""

from __future__ import annotations

from textual.theme import BUILTIN_THEMES, Theme

from peyk.platform.palette import RGB, Palette, contrast, mix, readable

TERMINAL = "terminal"
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN = range(1, 7)  # normal; the bright ones are 8 further
GREY = 8
SENDERS = (GREEN, CYAN, YELLOW, MAGENTA, BLUE)
# Our own variables, and what they are in the themes that come with Textual.
DEFAULTS = {
    "on-primary": "background",
    "bar": "panel",
    "edge": "primary",
    "sender-0": "success",
    "sender-1": "secondary",
    "sender-2": "warning",
    "sender-3": "accent",
    "sender-4": "error",
    "sender-5": "text",
}


def terminal_theme(palette: Palette | None) -> Theme:
    """Without a palette, the colours are named and the terminal fills them in."""
    if palette is None:
        return _theme(
            primary="ansi_bright_cyan",
            secondary="ansi_cyan",
            accent="ansi_bright_blue",
            warning="ansi_bright_yellow",
            error="ansi_bright_red",
            success="ansi_bright_green",
            muted="ansi_bright_black",
            line="ansi_bright_black",
            raised="#333333",
            on_primary="ansi_black",
            edge="ansi_cyan",
            senders=("ansi_green", "ansi_cyan", "ansi_yellow", "ansi_magenta", "ansi_bright_blue"),
            dark=True,
        )
    base, text = palette.base, palette.foreground
    # As PowerShell does it: the bright ones on a dark background.
    step = 8 if palette.dark else 0

    def colour(number: int) -> str:
        return _hex(readable(palette.colours[number + step], base))

    grey = palette.colours[GREY]
    primary = readable(palette.colours[CYAN + step], base)
    return _theme(
        primary=_hex(primary),
        secondary=_hex(readable(palette.colours[CYAN], base)),
        accent=_hex(readable(palette.colours[CYAN], base)),
        warning=colour(YELLOW),
        error=colour(RED),
        success=colour(GREEN),
        muted=_hex(readable(grey if contrast(grey, text) > 1.3 else mix(text, base, 0.4), base)),
        line=_hex(mix(base, text, 0.4)),
        raised=_hex(mix(base, text, 0.18)),
        on_primary=_hex(base if contrast(base, primary) > 4 else text),
        edge=_hex(readable(palette.colours[CYAN], base, 3)),
        # Not the bright ones: a name is not a warning.
        senders=tuple(_hex(readable(palette.colours[number], base)) for number in SENDERS),
        dark=palette.dark,
    )


def _theme(
    *,
    primary: str,
    secondary: str,
    accent: str,
    warning: str,
    error: str,
    success: str,
    muted: str,
    line: str,
    raised: str,
    on_primary: str,
    edge: str,
    senders: tuple[str, ...],
    dark: bool,
) -> Theme:
    return Theme(
        TERMINAL,
        primary=primary,
        secondary=secondary,
        accent=accent,
        warning=warning,
        error=error,
        success=success,
        # The terminal's own, so that a see-through window stays see-through.
        background="ansi_default",
        foreground="ansi_default",
        dark=dark,
        ansi=True,
        variables={
            **BUILTIN_THEMES["ansi-dark" if dark else "ansi-light"].variables,
            "text-muted": muted,
            "surface": raised,
            "panel": line,
            "bar": raised,
            "on-primary": on_primary,
            "edge": edge,
            "sender-5": "ansi_default",
            **{f"sender-{i}": colour for i, colour in enumerate(senders)},
            "border": primary,
            "border-blurred": line,
            "scrollbar": line,
            "scrollbar-background": "ansi_default",
            "scrollbar-background-hover": "ansi_default",
            "scrollbar-background-active": "ansi_default",
        },
    )


def _hex(colour: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)
