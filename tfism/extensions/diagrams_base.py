"""
tfsm.extensions.diagrams_base
------------------------------------

The class BaseGraph implements the common ground for Graphviz backends.
"""

import abc
import copy
import logging
from collections.abc import Generator, Iterator
from typing import TYPE_CHECKING, Any, Protocol, Union, cast

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())

# Type checking imports to avoid circular imports
if TYPE_CHECKING:
    from .diagrams import GraphMachine

# Graph object type - can be from different backends (pygraphviz, graphviz, etc.)
GraphObject = Any | None

# Type for state classes that have a separator attribute
StateClassWithSeparator = Any


class ContextManagerMachine(Protocol):
    """Protocol for machines that can be used as context managers.
    This is used by HierarchicalMachine to traverse nested states.
    """

    def __call__(self, state: Any) -> "ContextManagerMachine":
        """Enter a state context."""
        ...

    def __enter__(self) -> "ContextManagerMachine":
        """Enter the context manager."""
        ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context manager."""
        ...

    def get_global_name(self) -> str:
        """Get the global name."""
        ...

    def get_markup_config(self) -> Any:
        """Get the markup configuration."""
        ...

    def _get_enum_path(self, state: Any) -> list[Any]:
        """Get the enum path for a state."""
        ...

    @property
    def state_cls(self) -> Any:
        """Get the state class."""
        ...

    @property
    def show_state_attributes(self) -> bool:
        """Whether to show state attributes in graphs."""
        ...

    @property
    def show_conditions(self) -> bool:
        """Whether to show conditions in tfsm."""
        ...


class BaseGraph(abc.ABC):
    """Provides the common foundation for graphs generated either with pygraphviz or graphviz. This abstract class
    should not be instantiated directly. Use .(py)graphviz.(Nested)Graph instead.
    Attributes:
        machine (GraphMachine): The associated GraphMachine
        fsm_graph (object): The AGraph-like object that holds the graphviz information
    """

    def __init__(self, machine: Union["GraphMachine", "ContextManagerMachine"]) -> None:
        # Use Union to support both plain GraphMachine and hierarchical variants
        self.machine: GraphMachine | ContextManagerMachine = machine
        self.fsm_graph: GraphObject = None
        self.generate()

    @abc.abstractmethod
    def generate(self) -> None:
        """Triggers the generation of a graph."""

    @abc.abstractmethod
    def set_previous_transition(self, src: str, dst: str) -> None:
        """Sets the styling of an edge to 'previous'
        Args:
            src (str): Name of the source state
            dst (str): Name of the destination
        """

    @abc.abstractmethod
    def reset_styling(self) -> None:
        """Resets the styling of the currently generated graph."""

    @abc.abstractmethod
    def set_node_style(self, state: Any, style: str) -> None:
        """Sets the style of nodes associated with a model state
        Args:
            state (str, Enum or list): Name of the state(s) or Enum(s)
            style (str): Name of the style
        """

    @abc.abstractmethod
    def get_graph(self, title: str | None = None, roi_state: Any | None = None) -> GraphObject:
        """Returns a graph object.
        Args:
            title (str): Title of the generated graph
            roi_state (State): If not None, the returned graph will only contain edges and states connected to it.
        Returns:
             A graph instance with a `draw` that allows to render the graph.
        """

    def _convert_state_attributes(self, state: dict[str, Any]) -> str:
        """Convert state attributes to a label string for graph visualization.
        Args:
            state: State dictionary containing name, labels, tags, callbacks, etc.
        Returns:
            Formatted label string for the state node.
        """
        label: str = state.get("label", state["name"])
        if self.machine.show_state_attributes:
            if "tags" in state:
                label += " [" + ", ".join(state["tags"]) + "]"
            if "on_enter" in state:
                label += r"\l- enter:\l  + " + r"\l  + ".join(state["on_enter"])
            if "on_exit" in state:
                label += r"\l- exit:\l  + " + r"\l  + ".join(state["on_exit"])
            if "timeout" in state:
                label += r"\l- timeout(" + state["timeout"] + "s) -> (" + ", ".join(state["on_timeout"]) + ")"
        # end each label with a left-aligned newline
        return label + r"\l"

    def _get_state_names(self, state: Any) -> Generator[str, None, None]:
        """Recursively get state names from a state or collection of states.
        Args:
            state: A state object, Enum, or collection of states
        Yields:
            State names as strings
        """
        if isinstance(state, (list, tuple, set)):
            for res in state:
                for inner in self._get_state_names(res):
                    yield inner
        else:
            if hasattr(state, "name"):
                # Get separator from state_cls (use getattr for type safety)
                separator = getattr(self.machine.state_cls, "separator", "_")
                path = self.machine._get_enum_path(state)
                yield separator.join(path)
            else:
                yield str(state)

    def _transition_label(self, tran: dict[str, Any]) -> str:
        """Generate a label for a transition edge in the graph.
        Args:
            tran: Transition dictionary containing trigger, conditions, etc.
        Returns:
            Formatted label string for the transition edge.
        """
        edge_label: str = tran.get("label", tran["trigger"])
        if "dest" not in tran:
            edge_label += " [internal]"
        if self.machine.show_conditions and any(prop in tran for prop in ["conditions", "unless"]):
            edge_label = "{edge_label} [{conditions}]".format(
                edge_label=edge_label,
                conditions=" & ".join(tran.get("conditions", []) + ["!" + u for u in tran.get("unless", [])]),
            )
        return edge_label

    def _get_global_name(self, path: list[Any]) -> str:
        """Get the global name by traversing the path.
        Args:
            path: List of state names representing the path
        Returns:
            The global name as a string
        """
        if path:
            state = path.pop(0)
            # machine conforms to ContextManagerMachine protocol when hierarchical
            # This is used in HierarchicalGraphMachine to traverse nested states
            machine_with_context = cast(ContextManagerMachine, self.machine)
            with machine_with_context(state):
                return self._get_global_name(path)
        else:
            result: str = self.machine.get_global_name()
            return result

    def _flatten(self, *lists: Any) -> Iterator[Any]:
        """Flatten nested lists/tuples into a single iterator.
        Args:
            *lists: Variable number of lists, tuples, or state objects to flatten
        Yields:
            Flattened elements (state names or state objects)
        """
        return (
            e for a in lists for e in (self._flatten(*a) if isinstance(a, (tuple, list)) else (a.name if hasattr(a, "name") else str(a),))
        )

    def _get_elements(self) -> tuple[list[Any], list[dict[str, Any]]]:
        """Extract states and tfsm from the machine's markup configuration.
        Returns:
            A tuple containing:
                - List of state objects/dictionaries
                - List of transition dictionaries
        """
        states: list[Any] = []
        transitions: list[dict[str, Any]] = []
        try:
            markup: dict[str, Any] = self.machine.get_markup_config()
            queue: list[tuple[list[str], Any]] = [([], markup)]

            # Get separator once for efficiency
            separator = getattr(self.machine.state_cls, "separator", "_")

            while queue:
                prefix, scope = queue.pop(0)
                for transition in scope.get("tfsm", []):
                    if prefix:
                        tran = copy.copy(transition)
                        tran["source"] = separator.join(prefix + [tran["source"]])
                        if "dest" in tran:  # don't do this for internal tfsm
                            tran["dest"] = separator.join(prefix + [tran["dest"]])
                    else:
                        tran = transition
                    transitions.append(tran)
                for state in scope.get("children", []) + scope.get("states", []):
                    if not prefix:
                        states.append(state)

                    ini = state.get("initial", [])
                    if not isinstance(ini, list):
                        ini = ini.name if hasattr(ini, "name") else ini
                        tran = dict(
                            trigger="",
                            source=separator.join(prefix + [state["name"]]),
                            dest=separator.join(prefix + [state["name"], ini]),
                        )
                        transitions.append(tran)
                    if state.get("children", []):
                        queue.append((prefix + [state["name"]], state))
        except KeyError:
            _LOGGER.error("Graph creation incomplete!")
        return states, transitions
