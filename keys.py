"""Windows virtual-key codes for NULLSIGHT input.

Hyperlite only exposes a handful of named keys; we define the rest here and
pass integer VK codes directly to ``engine.key_down``.
"""

# Movement
W = 0x57
A = 0x41
S = 0x53
D = 0x44

# Actions
SPACE = 0x20
TAB = 0x09
RETURN = 0x0D
ESCAPE = 0x1B
SHIFT = 0x10
F11 = 0x7A
R = 0x52
E = 0x45

# Arrow keys (look fallback when the cursor is not captured)
LEFT = 0x25
UP = 0x26
RIGHT = 0x27
DOWN = 0x28
