# AWX Lab terminal design

## Overview

AWX Lab is a keyboard-operated native terminal console for building and testing a local AWX environment. Bare `awx-lab` opens the command console and detailed logs without starting a deployment. Direct CLI commands retain their existing behavior. The surface uses Python curses and character cells; terminal settings supply the font and base colors.

## Colors

- **Default foreground and background:** body text, console output and logs inherit the terminal palette. The block wordmark's AWX portion uses the default foreground in bold, appearing white in the approved dark-terminal preview; white is not hard-coded.
- **Cyan accent:** `curses.COLOR_CYAN` marks LAB, command headings, the input prefix, active operations, echoed commands and recognized successful result/login lines. The accent uses the default background (`-1`); if curses cannot enable default colors, its color pair falls back to a black background. Exact RGB values belong to the terminal palette.
- **Dim secondary text:** `curses.A_DIM` distinguishes the tagline, version, source path/branch, inactive operation text, dividers, notices and keyboard hints. Focus and state also have text labels, so they do not depend solely on color.

## Typography

Use the terminal's fixed-cell font without prescribing a typeface or pixel size. At 24 rows or taller, the two-row block-character AWX LAB wordmark sits above `BUILD · TEST · REPEAT`; AWX is bold default foreground and LAB is bold cyan. Shorter usable screens show a single cyan title line with the name, version and tagline. Body output stays at normal intensity; bold is reserved for the wordmark and command heading. Text clipping and wrapping respect character-cell widths.

## Layout

| Terminal size | Behavior |
| --- | --- |
| At least 100 columns | Commands on the left; live logs on the right; divider at `int(width * 0.45)` |
| 44–99 columns | One transcript pane at a time, selected with Tab and identified by bracketed tab labels |
| At least 24 rows | Two-row block wordmark and separate tagline |
| 14–23 rows | Compact single-line title |
| Below 44 columns or 14 rows | Resize notice with cancellation/exit hints |

The shared header separates saved environment/runtime state from the current operation and elapsed time, then identifies the launch directory and Git branch. Saved state is explicitly labeled; it is not a live health check. Console text wraps, while logs preserve long lines for horizontal panning. Input, notices and keyboard hints occupy the bottom rows. Below 80 columns, the footer omits the scrolling hint to conserve space.

## Elevation & Depth

The interface is flat. Dim line separators, spacing and labeled regions establish hierarchy without shadows or filled panels.

## Shapes

Use character-cell horizontal rules and a single vertical pane divider. Narrow layouts use text tabs with square brackets around the selected pane.

## Components

- **Console and focus:** start in Commands. Tab switches focus and the visible narrow pane; Esc returns to input and dismisses an exit choice. The insertion cursor appears only with console focus. F2 completes commands/options; Up/Down recalls session history; standard cursor keys edit input. This accepts AWX Lab commands only, without shell evaluation. Local tests always run from the launch directory.
- **Live logs:** start following output. Scrolling pauses following and labels the pane `PAUSED · End resumes`; End or `follow` restores it. Left/Right pans logs. The log reader follows rotation and lab selection; commands without a lab log can display their own output. Empty logs show an instructional message.
- **Operation state:** only one command runs at a time. Help, saved status, log navigation and cancellation remain available. Completion reports completed, interrupted or failed with the exit code. Successful AWX login summaries and detailed logs intentionally include the username/password.
- **Installer input:** recognized approval, password and Enter prompts use the console input. Password input is masked and excluded from history. Prompt replies remain distinct from AWX commands.
- **Cancellation and exit:** Ctrl+C interrupts the active CLI and allows cleanup; it clears idle input otherwise. Idle exit keeps the lab. Exit during work offers `cancel` or `stay`; cancellation waits for the CLI before closing. Neither action implicitly stops or purges the lab.

## Source of truth

[awx_lab/tui.py](awx_lab/tui.py) owns the wordmark, palette, layout, keyboard handling and console states. [awx_lab/terminal.py](awx_lab/terminal.py) owns bounded text buffers, terminal-control sanitization, log following and subprocess terminal behavior. The [terminal guide](docs/terminal-interface.md) documents the user-facing controls and limitations. Preview images are illustrative captures of the same drawing function, not evidence of a completed deployment.
