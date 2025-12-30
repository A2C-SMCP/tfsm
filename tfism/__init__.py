"""
tfsm
-----------

A lightweight, object-oriented state machine implementation in Python. Requires Python 3.11+.
"""

from .core import Event, EventData, Machine, MachineError, State, Transition
from .version import __version__

__all__ = [
    "__version__",
    "State",
    "Transition",
    "Event",
    "EventData",
    "Machine",
    "MachineError",
]

__copyright__ = "Copyright (c) 2024 Tal Yarkoni, Alexander Neumann"
__license__ = "MIT"
__summary__ = "A lightweight, object-oriented finite state machine implementation in Python with many extensions"
__uri__ = "https://github.com/pytransitions/transitions"
