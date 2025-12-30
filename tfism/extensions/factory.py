"""
tfsm.extensions.factory
------------------------------

This module contains the definitions of classes which combine the functionality of tfsm'
extension modules. These classes can be accessed by names as well as through a static convenience
factory object.
"""

import itertools
from collections.abc import Callable
from functools import partial
from typing import Any

from ..core import Machine, Transition
from .diagrams import GraphMachine, HierarchicalGraphMachine, NestedGraphTransition
from .locking import LockedMachine
from .nesting import HierarchicalMachine, NestedEvent, NestedTransition

try:
    from tfism.extensions.asyncio import AsyncMachine, AsyncTransition, HierarchicalAsyncMachine, NestedAsyncTransition
except (ImportError, SyntaxError):  # pragma: no cover

    class AsyncMachine(Machine):  # type: ignore
        """A mock of AsyncMachine for Python 3.6 and earlier."""

    class AsyncTransition(Transition):  # type: ignore
        """A mock of AsyncTransition for Python 3.6 and earlier."""

    class HierarchicalAsyncMachine(HierarchicalMachine):  # type: ignore
        """A mock of HierarchicalAsyncMachine for Python 3.6 and earlier."""

    class NestedAsyncTransition(NestedTransition):  # type: ignore
        """A mock of NestedAsyncTransition for Python 3.6 and earlier."""


class MachineFactory:
    """Convenience factory for machine class retrieval."""

    # get one of the predefined classes which fulfill the criteria
    @staticmethod
    def get_predefined(graph: bool = False, nested: bool = False, locked: bool = False, asyncio: bool = False) -> type[Machine]:
        """A function to retrieve machine classes by required functionality.
        Args:
            graph (bool): Whether the returned class should contain graph support.
            nested: Whether the returned machine class should support nested states.
            locked: Whether the returned class should facilitate locks for threadsafety.

        Returns (class): A machine class with the specified features.
        """
        try:
            return _CLASS_MAP[(graph, nested, locked, asyncio)]
        except KeyError:
            raise ValueError("Feature combination not (yet) supported")  # from KeyError


class LockedHierarchicalMachine(LockedMachine, HierarchicalMachine):
    """
    A threadsafe hierarchical machine.
    """

    event_cls = NestedEvent  # type: ignore[assignment]

    def _get_qualified_state_name(self, state: Any) -> str:
        result = self.get_global_name(state.name)
        return result if isinstance(result, str) else result[0]


class LockedGraphMachine(GraphMachine, LockedMachine):  # type: ignore[misc]
    """
    A threadsafe machine with graph support.
    """

    @staticmethod
    def format_references(func: Callable[..., Any] | partial[Any]) -> str:
        if isinstance(func, partial) and func.func.__name__.startswith("_locked_method"):
            return "%s(%s)" % (
                func.args[0].__name__,
                ", ".join(
                    itertools.chain(
                        (str(_) for _ in func.args[1:]),
                        ("%s=%s" % (key, value) for key, value in (func.keywords if func.keywords else {}).items()),
                    )
                ),
            )
        return GraphMachine.format_references(func)


class LockedHierarchicalGraphMachine(GraphMachine, LockedHierarchicalMachine):  # type: ignore[misc]
    """
    A threadsafe hierarchical machine with graph support.
    """

    transition_cls = NestedGraphTransition
    event_cls = NestedEvent

    @staticmethod
    def format_references(func: Callable[..., Any] | partial[Any]) -> str:
        return LockedGraphMachine.format_references(func)


class AsyncGraphMachine(GraphMachine, AsyncMachine):
    """A machine that supports asynchronous event/callback processing with Graphviz support."""

    transition_cls = AsyncTransition  # type: ignore[assignment]


class HierarchicalAsyncGraphMachine(GraphMachine, HierarchicalAsyncMachine):
    """A hierarchical machine that supports asynchronous event/callback processing with Graphviz support."""

    transition_cls = NestedAsyncTransition  # type: ignore[assignment]


# 4d tuple (graph, nested, locked, async)
_CLASS_MAP: dict[tuple[bool, bool, bool, bool], type[Machine]] = {
    (False, False, False, False): Machine,
    (False, False, True, False): LockedMachine,
    (False, True, False, False): HierarchicalMachine,
    (False, True, True, False): LockedHierarchicalMachine,
    (True, False, False, False): GraphMachine,
    (True, False, True, False): LockedGraphMachine,
    (True, True, False, False): HierarchicalGraphMachine,
    (True, True, True, False): LockedHierarchicalGraphMachine,
    (False, False, False, True): AsyncMachine,
    (True, False, False, True): AsyncGraphMachine,
    (False, True, False, True): HierarchicalAsyncMachine,
    (True, True, False, True): HierarchicalAsyncGraphMachine,
}
