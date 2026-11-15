# Course Editor

Pygame-based course editor (`golf-editor`). The data it edits is described in
`docs/course_data.md`.

- `core/` - Pygame rendering primitives (tilesets, sprites)
- `ui/` - widgets, dialogs, tile pickers, toolbar
- `controllers/` - editor state, event handling, view state, undo management
- `rendering/` - specialized renderers (terrain, greens, grid, sprites)
- `tools/` - editor tools (paint, the brushes, eyedropper, forest fill, etc.)
- `algorithms/` - Green Fix; algorithms the randomizer shares, forest fill among them,
  are in `golf/algorithms/`

A standalone executable is built with PyInstaller from the repo root:
`uv run pyinstaller run_editor.spec`.

## Three-layer architecture

The editor uses a **strict three-layer architecture**. Violating these boundaries causes
architectural drift and should be avoided.

### Layer 1: Input translation (EventHandler)

`editor/controllers/event_handler.py` translates pygame events into high-level actions.

- MUST route events to the right handler (tools, pickers, toolbar), call Application
  callbacks for state-changing operations, and delegate tool operations to ToolManager.
- MUST NOT modify EditorState, HoleData or any application state directly, implement tool
  logic, or decide what state to change.

```python
# GOOD: delegate to callback
if key == pygame.K_TAB:
    self.on_toggle_mode()

# BAD: change state directly
if key == pygame.K_TAB:
    self.state.mode = "terrain"  # WRONG LAYER!
```

### Layer 2: Coordination (Application)

`editor/application.py` coordinates components and manages state.

- MUST handle EventHandler callbacks, update EditorState and HoleData, delegate operations
  to tools via ToolManager, and create the context
  objects (ViewState, RenderContext, HighlightState) used for rendering.
- MUST NOT handle pygame events directly or implement tool logic.

```python
# GOOD: coordination
def _set_mode(self, mode: str):
    self.state.mode = mode
    self._update_mode_buttons()

# BAD: tool implementation
def _paint_tile(self, pos):
    tile = self._screen_to_tile(pos)  # Tool logic doesn't belong here
```

### Layer 3: Execution (Tools)

`editor/tools/*.py` execute specific editing operations.

- MUST receive a ToolContext, execute the operation, return a ToolResult saying whether
  the event was handled, and own their own state (e.g. StampTool owns StampToolState).
- MUST NOT access Application or EventHandler, or handle raw pygame events.

```python
# GOOD: tool execution
def handle_mouse_down(self, pos, button, modifiers, context):
    tile = context.view_state.screen_to_tile(pos)
    if tile:
        context.hole_data.set_terrain_tile(row, col, value)
        return ToolResult.modified()
```

## Context objects

- **ViewState** (`controllers/view_state.py`) - viewport camera (offset_x, offset_y,
  scale) and coordinate conversions (`screen_to_tile()`, `tile_to_screen()`, ...).
  **Always use these for coordinate conversion**; never re-derive the math. Tools get
  one from `context.view_state`, a property that builds a fresh ViewState from the
  context's screen size and the editor state's offsets and zoom on each access. The
  canvas rectangle (right of the tile picker, below the toolbar, above the status bar,
  excluding the tool picker column) is defined once, by `canvas_rect(screen_width,
  screen_height)` in the same module; Application uses it too, so a position over the
  tool picker converts to `None`.
- **RenderContext** (`rendering/render_context.py`) - rendering resources (tileset,
  sprites, mode) and settings (grid_mode, show_sprites, selected_flag_index).
- **HighlightState** (`controllers/highlight_state.py`) - temporary highlights (hover,
  brush strokes), updated via callbacks rather than polled.

## State

- **EditorState** (`controllers/editor_state.py`) - mode, canvas offset, zoom, selected
  palette, flags. Managed by Application, never by EventHandler or tools.
- **HoleData** (`golf/formats/hole_data.py`) - the course data. Modified by tools via
  ToolContext.
- **UndoManager** (`controllers/undo_manager.py`) - snapshot undo/redo. Tools call
  `context.state.undo_manager.push_state(context.hole_data)` *before* modifying data,
  and only when something will change: a step that changes nothing still marks the hole
  modified when undone. `write_tiles(context, cells)` in `tools/base_tool.py` writes
  tiles and pushes only if one changes; a change that can only tell afterwards takes
  `snapshot()` first and `push_snapshot()`s it if it changed anything (the Metadata
  Editor). The manager also remembers the hole as last loaded or saved, so undo back to
  it clears `modified`.

## Common pitfalls

- **State changes in EventHandler.** `self.state.mode = "greens"` in a key handler is
  wrong; call `self.on_toggle_mode()` and let Application change it.
- **Duplicated coordinate math.** Don't compute `(pos[0] - canvas_rect.x + offset_x) //
  tile_size` or build a `Rect` from layout constants; use
  `context.view_state.screen_to_tile(pos)`.
- **Bypassing ToolManager.** Don't call a specific tool's `handle_mouse_down` directly;
  get `self.tool_manager.get_active_tool()` and route through it.
- **Recreating UI components on resize.** Update positions (`self.toolbar.resize(width)`)
  instead of rebuilding buttons, which leaks.
- **Polling in the render loop.** Don't query key state or picker hover every frame; use
  callbacks such as `TilePicker(..., on_hover_change=self._on_hover)`.

## Adding editor tools

Every tool implements the Tool protocol in `tools/base_tool.py`: `handle_mouse_down`,
`handle_mouse_up`, `handle_mouse_motion`, `handle_key_down`, `handle_key_up`,
`on_activated`, `on_deactivated`, `reset`, `get_hotkey`. With no hole open, `EventHandler`
gives mouse events only to a tool with a `render_overlay()`, so the others can assume the
hole has terrain and greens when they get a click. Copy the closest existing tool
of the same kind rather than starting from scratch:

| Kind | Behavior | Example to copy |
|------|----------|-----------------|
| Modal | Persistent mode shown in the picker; stays active until another tool is chosen | `paint_tool.py`, `forest_fill_tool.py` |
| Service | Not in the picker; used by other tools via `context.get_eyedropper_tool()` | `eyedropper_tool.py` |
| Action | Picker button that runs immediately in `on_activated()` and leaves the previous tool active; `is_action_tool()` returns `True`; reports through `context.show_message()`, since `on_activated()` returns no ToolResult | `add_row_tool.py`, `remove_row_tool.py` |
| Dialog | Opens a modal dialog in `on_activated()`, shows as active while open, then calls `context.request_revert_to_previous_tool()` on close; draws via `render_overlay()` | `metadata_editor_tool.py` |

Register the tool in `Application.__init__`:

```python
self.tool_manager.register_tool("your_tool", YourTool())
self.tool_picker.register_tool("your_tool", "Your Tool", "🔧")            # modal / dialog
self.tool_picker.register_tool("your_action", "Your Action", "⚡", is_action=True)  # action
```

`is_action=True` makes the picker execute the tool on every click without changing the
selected tool, so an action can be repeated.

Best practices:

- Return `ToolResult.modified()` when data changes, `ToolResult.handled()` when the
  event is consumed without a change, and `ToolResult.not_handled()` to let other
  handlers see it. Either of the first two may carry a `message` for the status bar.
- Push undo state **before** modifying data.
- Keep tool-specific state as instance variables (e.g. `self.is_painting`).

## Keys

ToolManager validates hotkey uniqueness on registration and raises `ValueError` on a
conflict. Tool hotkeys currently in use: A, B, C, D, F, K, M, N, O, P, R, S, U, V, `=` (add row),
`-` (remove row); `grep -n -A2 'def get_hotkey' editor/tools/*.py` is the source of truth.
Also reserved: G (grid), Tab (mode), 1-3 (palette), F1-F4 (flag position), Ctrl+Z/Y
(undo/redo), Ctrl+N (new), Ctrl+O (load), Ctrl+S (save), Ctrl+Shift+S (save as),
Ctrl+C/X/V (the Selection tool's copy, cut and paste), Ctrl+Shift+C (create stamp),
`,`/`.` (brush radius, in the brushes). The editor and its file commands, for users, are
`docs/editor.md`.

## Tool-specific behavior

**Forest Fill**: clicking inside a forest placeholder region fills only that region, not
all regions, and the tool stays active for multiple clicks. Algorithm notes:
`docs/forest_notes.md`.

**Feature Brush**: paints fairways, bunkers and water as shapes in course pixels and
chooses the border tiles on release; the selected palette decides which. The tool only
gathers the stroke: the fit is `golf/algorithms/feature_fit.py` and applying a stroke to a
hole is `golf/algorithms/feature_brush.py`. It never writes over anything but bare ground,
the placeholder and the feature's own tiles. `,` and `.` set its radius while it is active. See
`docs/feature_brush.md` and ADR 0013.

**Out of Bounds Brush**: `BoundaryBrushTool`, beside the Feature Brush in the same file,
paints out-of-bounds ground and fits the line (`$80`-`$9B`) round it
(`golf/algorithms/boundary.py`, `boundary_change` in `feature_brush.py`). It leaves the
cells inside the line as placeholder for Forest Fill rather than filling them, so the
user can seed clearings first (ADR 0014). The placeholder is out of bounds to it and
bare ground to the Feature Brush.

**Green Brush**: `GreenBrushTool`, in the same file again, paints a green's putting
surface in greens mode, in the green's own pixels, and fits the fringe round it
(`green_change` in `feature_brush.py`, the zones of the greens tiles in
`golf/algorithms/green_zones.py`). It writes the fringe, flat putting surface and the
rough, so nothing has to be run after it; slopes survive except where the fringe moves
onto them. The greens renderer draws the same stroke overlay as the terrain renderer
(`render_feature_brush` in `rendering/highlight_utils.py`). See `docs/feature_brush.md`
and ADR 0020.

**Green Fix**: the action on `U`. It redoes every rough tile outside the fringe, keeping
the way the green's rough is checkered, and fills placeholder: rough where it is joined
to the grid's edge, flat putting surface elsewhere. The rough's rules are the Green
Brush's (`rough_tile` and `rough_phase` in `golf/algorithms/green_zones.py`).

**Stamps**: built-in stamps live in `data/stamps/built-in/`, user stamps in
`~/.config/golf-editor/stamps`. Everything under `hazard/` is generated by
`golf-hazard-stamps` from the enclosed bunkers and water hazards of the vanilla courses
(`golf/algorithms/hazard_shapes.py`), one stamp per distinct shape, filed by the longer
side of its bounding box; edit the generator, not the files. Sand and water share one
category because placing a stamp writes tiles only, never palettes. `teebox/` and `tree/`
are hand-made and complete: vanilla has no other tee boxes or single trees. The browser
lists a category smallest first.
