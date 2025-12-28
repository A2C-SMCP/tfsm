"""
tfsm.extensions
----------------------

Additional functionality such as hierarchical (nested) machine support, Graphviz-based diagram creation
and threadsafe execution of machine methods. Additionally, combinations of all those features are possible
and made easier to access with a convenience factory.
"""

from .diagrams import GraphMachine, HierarchicalGraphMachine
from .factory import LockedGraphMachine, LockedHierarchicalGraphMachine, LockedHierarchicalMachine, MachineFactory
from .locking import LockedMachine
from .nesting import HierarchicalMachine

try:
    # only available for Python 3
    from .asyncio import AsyncMachine, HierarchicalAsyncMachine
    from .factory import AsyncGraphMachine, HierarchicalAsyncGraphMachine
except (ImportError, SyntaxError):  # pragma: no cover
    pass

__all__ = [
    "GraphMachine",
    "HierarchicalGraphMachine",
    "HierarchicalMachine",
    "LockedMachine",
    "MachineFactory",
    "LockedHierarchicalGraphMachine",
    "LockedHierarchicalMachine",
    "LockedGraphMachine",
    "AsyncMachine",
    "HierarchicalAsyncMachine",
    "AsyncGraphMachine",
    "HierarchicalAsyncGraphMachine",
]
