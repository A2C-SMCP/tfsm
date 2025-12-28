"""
transitions
-----------

A lightweight, object-oriented state machine implementation in Python. Requires Python 3.11+.
"""
from .version import __version__
from .core import (State, Transition, Event, EventData, Machine, MachineError)

__copyright__ = "Copyright (c) 2024 Tal Yarkoni, Alexander Neumann"
__license__ = "MIT"
__summary__ = "A lightweight, object-oriented finite state machine implementation in Python with many extensions"
__uri__ = "https://github.com/pytransitions/transitions"
