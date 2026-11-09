"""
Visual tokens for the analyser.
"""

# Core palette
BG          = "#131720"   # case
PANEL       = "#1b212e"   # raised surfaces
PANEL_EDGE  = "#2a3345"
GRID        = "#242c3b"   # graticule
TEXT        = "#d8dee9"
MUTED       = "#798798"

VOLT        = "#f2b134"   # channel 1, amber
CURR        = "#45c8d8"   # channel 2, cyan
GOOD        = "#6fcf7f"
ALERT       = "#e2564d"

UI_FONT   = "Inter, Roboto, DejaVu Sans, Sans Serif"
MONO_FONT = "JetBrains Mono, DejaVu Sans Mono, Monospace"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {UI_FONT};
    font-size: 15px;
}}

/* Labels inherit the window colour by default, which paints dark
   rectangles inside the lighter tiles. Make them transparent so they take
   whatever surface they sit on. */
QLabel {{
    background: transparent;
}}
#Mini {{
    background: transparent;
}}

#Header {{
    background: {BG};
    border-bottom: 1px solid {PANEL_EDGE};
}}
#Title {{
    font-size: 19px;
    font-weight: 600;
    letter-spacing: 0.2px;
}}
#Subtitle {{
    color: {MUTED};
    font-size: 13px;
}}

#Sidebar {{
    background: {BG};
    border-left: 1px solid {PANEL_EDGE};
}}

/* Value tiles ------------------------------------------------------- */
#Tile {{
    background: {PANEL};
    border: 1px solid {PANEL_EDGE};
    border-radius: 8px;
}}
#TileLabel {{
    color: {MUTED};
    font-size: 13px;
}}
#TileValue {{
    font-family: {MONO_FONT};
    font-size: 30px;
    font-weight: 600;
}}
#TileUnit {{
    color: {MUTED};
    font-family: {MONO_FONT};
    font-size: 15px;
}}

/* Compact readouts -------------------------------------------------- */
#MiniLabel {{
    color: {MUTED};
    font-size: 12px;
}}
#MiniValue {{
    font-family: {MONO_FONT};
    font-size: 18px;
}}

/* Controls ---------------------------------------------------------- */
QPushButton {{
    background: {PANEL};
    border: 1px solid {PANEL_EDGE};
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 15px;
}}
QPushButton:hover  {{ background: #222b3b; }}
QPushButton:pressed{{ background: #2c374a; }}
QPushButton:checked {{
    background: #243244;
    border-color: {CURR};
    color: {CURR};
}}
QPushButton#Danger:pressed {{ background: {ALERT}; color: {BG}; }}

/* Status strip ------------------------------------------------------ */
#Status {{
    background: {BG};
    border-top: 1px solid {PANEL_EDGE};
    color: {MUTED};
    font-family: {MONO_FONT};
    font-size: 13px;
}}
#StatusOk    {{ color: {GOOD}; font-family: {MONO_FONT}; font-size: 13px; }}
#StatusAlert {{ color: {ALERT}; font-family: {MONO_FONT}; font-size: 13px; }}
"""
