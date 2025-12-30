"""
tfsm.extensions.diagrams
-------------------------------

This module contains machine and transition definitions for generating diagrams from machine instances.
It uses Graphviz either directly with the help of pygraphviz (https://pygraphviz.github.io/) or loosely
coupled via dot graphs with the graphviz module (https://github.com/xflr6/graphviz).
Pygraphviz accesses libgraphviz directly and also features more functionality considering graph manipulation.
However, especially on Windows, compiling the required extension modules can be tricky.
Furthermore, some pygraphviz issues are platform-dependent as well.
Graphviz generates a dot graph and calls the `dot` executable to generate diagrams and thus is commonly easier to
set up. Make sure that the `dot` executable is in your PATH.
"""

import logging
import warnings
from functools import partial
from typing import Any

from tfism import Transition

from ..core import EventData, listify
from .markup import HierarchicalMarkupMachine, MarkupMachine
from .nesting import NestedTransition

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())


class TransitionGraphSupport(Transition):
    """Transition used in conjunction with (Nested)Graphs to update graphs whenever a transition is
    conducted.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        label = kwargs.pop("label", None)
        super().__init__(*args, **kwargs)
        if label:
            self.label = label

    def _change_state(self, event_data: EventData) -> None:
        graph = event_data.machine.model_graphs[id(event_data.model)]
        graph.reset_styling()
        graph.set_previous_transition(self.source, self.dest)
        super()._change_state(event_data)  # pylint: disable=protected-access
        graph = event_data.machine.model_graphs[id(event_data.model)]  # graph might have changed during change_event
        graph.set_node_style(getattr(event_data.model, event_data.machine.model_attribute), "active")


class GraphMachine(MarkupMachine):
    """Extends tfsm.core.Machine with graph support.
    Is also used as a mixin for HierarchicalMachine.
    Attributes:
        _pickle_blacklist (list): Objects that should not/do not need to be pickled.
        transition_cls (cls): TransitionGraphSupport
    """

    _pickle_blacklist = ["model_graphs"]
    transition_cls = TransitionGraphSupport

    machine_attributes = {
        "directed": "true",
        "strict": "false",
        "rankdir": "LR",
    }

    style_attributes = {
        "node": {
            "default": {
                "style": "rounded,filled",
                "shape": "rectangle",
                "fillcolor": "white",
                "color": "black",
                "peripheries": "1",
            },
            "inactive": {"fillcolor": "white", "color": "black", "peripheries": "1"},
            "parallel": {
                "shape": "rectangle",
                "color": "black",
                "fillcolor": "white",
                "style": "dashed, rounded, filled",
                "peripheries": "1",
            },
            "active": {"color": "red", "fillcolor": "darksalmon", "peripheries": "2"},
            "previous": {"color": "blue", "fillcolor": "azure", "peripheries": "1"},
        },
        "edge": {"default": {"color": "black"}, "previous": {"color": "blue"}},
        "graph": {
            "default": {"color": "black", "fillcolor": "white", "style": "solid"},
            "previous": {"color": "blue", "fillcolor": "azure", "style": "filled"},
            "active": {"color": "red", "fillcolor": "darksalmon", "style": "filled"},
            "parallel": {"color": "black", "fillcolor": "white", "style": "dotted"},
        },
    }

    # model_graphs cannot be pickled. Omit them.
    def __getstate__(self) -> dict[str, Any]:
        # self.pkl_graphs = [(g.markup, g.custom_styles) for g in self.model_graphs]
        return {k: v for k, v in self.__dict__.items() if k not in self._pickle_blacklist}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.model_graphs: dict[int, Any] = {}  # reinitialize new model_graphs
        for model in self.models:
            try:
                _ = self._get_graph(model)
            except AttributeError as err:
                _LOGGER.warning("Graph for model could not be initialized after pickling: %s", err)

    def __init__(
        self,
        model: Any = MarkupMachine.self_literal,
        states: Any | None = None,
        initial: str = "initial",
        transitions: Any | None = None,
        send_event: bool = False,
        auto_transitions: bool = True,
        ordered_transitions: bool = False,
        ignore_invalid_triggers: Any | None = None,
        before_state_change: Any | None = None,
        after_state_change: Any | None = None,
        name: Any | None = None,
        queued: bool = False,
        prepare_event: Any | None = None,
        finalize_event: Any | None = None,
        model_attribute: str = "state",
        model_override: bool = False,
        on_exception: Any | None = None,
        on_final: Any | None = None,
        title: str = "State Machine",
        show_conditions: bool = False,
        show_state_attributes: bool = False,
        show_auto_transitions: bool = False,
        use_pygraphviz: bool = True,
        graph_engine: str = "pygraphviz",
        **kwargs: Any,
    ) -> None:
        # remove graph config from keywords
        self.title = title
        self.show_conditions = show_conditions
        self.show_state_attributes = show_state_attributes
        # in MarkupMachine this switch is called 'with_auto_transitions'
        # keep 'auto_transitions_markup' for backwards compatibility
        kwargs["auto_transitions_markup"] = show_auto_transitions
        self.model_graphs: dict[int, Any] = {}  # type: ignore[no-redef]
        if use_pygraphviz is False:
            warnings.warn("Please replace 'use_pygraphviz=True' with graph_engine='graphviz'.", category=DeprecationWarning)
            graph_engine = "graphviz"
        self.graph_cls = self._init_graphviz_engine(graph_engine)

        _LOGGER.debug("Using graph engine %s", self.graph_cls)
        super().__init__(
            model=model,
            states=states,
            initial=initial,
            transitions=transitions,
            send_event=send_event,
            auto_transitions=auto_transitions,
            ordered_transitions=ordered_transitions,
            ignore_invalid_triggers=ignore_invalid_triggers,
            before_state_change=before_state_change,
            after_state_change=after_state_change,
            name=name,
            queued=queued,
            prepare_event=prepare_event,
            finalize_event=finalize_event,
            model_attribute=model_attribute,
            model_override=model_override,
            on_exception=on_exception,
            on_final=on_final,
            **kwargs,
        )

        # for backwards compatibility assign get_combined_graph to get_graph
        # if model is not the machine
        if not hasattr(self, "get_graph"):
            self.get_graph = self.get_combined_graph

    def _init_graphviz_engine(self, graph_engine: str) -> type[Any]:
        """Imports diagrams (py)graphviz backend based on machine configuration"""
        is_hsm = issubclass(self.transition_cls, NestedTransition)
        if graph_engine == "pygraphviz":
            from .diagrams_pygraphviz import (  # pylint: disable=import-outside-toplevel
                Graph as PyGraphvizGraph,
            )
            from .diagrams_pygraphviz import (
                NestedGraph as PyGraphvizNestedGraph,
            )
            from .diagrams_pygraphviz import (  # type: ignore[attr-defined]
                pgv,
            )

            if pgv:
                return PyGraphvizNestedGraph if is_hsm else PyGraphvizGraph
            _LOGGER.warning("Could not import pygraphviz backend. Will try graphviz backend next.")
            graph_engine = "graphviz"

        if graph_engine == "graphviz":
            from .diagrams_graphviz import (  # pylint: disable=import-outside-toplevel
                Graph as GraphvizGraph,
            )
            from .diagrams_graphviz import (
                NestedGraph as GraphvizNestedGraph,
            )
            from .diagrams_graphviz import (  # type: ignore[attr-defined]
                pgv,
            )

            if pgv:
                return GraphvizNestedGraph if is_hsm else GraphvizGraph
            _LOGGER.warning("Could not import graphviz backend. Fallback to mermaid graphs")

        from .diagrams_mermaid import Graph as MermaidGraph
        from .diagrams_mermaid import NestedGraph as MermaidNestedGraph  # pylint: disable=import-outside-toplevel

        return MermaidNestedGraph if is_hsm else MermaidGraph

    def _get_graph(self, model: Any, title: str | None = None, force_new: bool = False, show_roi: bool = False) -> Any:
        """This method will be bound as a partial to models and return a graph object to be drawn or manipulated.
        Args:
            model (object): The model that `_get_graph` was bound to. This parameter will be set by `GraphMachine`.
            title (str): The title of the created graph.
            force_new (bool): Whether a new graph should be generated even if another graph already exists. This should
            be true whenever the model's state or machine's tfsm/states/events have changed.
            show_roi (bool): If set to True, only render states that are active and/or can be reached from
                the current state.
        Returns: AGraph (pygraphviz) or Digraph (graphviz) graph instance that can be drawn.
        """
        if force_new:
            graph = self.graph_cls(self)
            self.model_graphs[id(model)] = graph
            try:
                graph.set_node_style(getattr(model, self.model_attribute), "active")
            except AttributeError:
                _LOGGER.info("Could not set active state of diagram")
        try:
            graph = self.model_graphs[id(model)]
        except KeyError:
            _ = self._get_graph(model, title, force_new=True)
            graph = self.model_graphs[id(model)]
        return graph.get_graph(title=title, roi_state=getattr(model, self.model_attribute) if show_roi else None)

    def get_combined_graph(self, title: str | None = None, force_new: bool = False, show_roi: bool = False) -> Any:
        """This method is currently equivalent to 'get_graph' of the first machine's model.
        In future releases of tfsm, this function will return a combined graph with active states
        of all models.
        Args:
            title (str): Title of the resulting graph.
            force_new (bool): Whether a new graph should be generated even if another graph already exists. This should
            be true whenever the model's state or machine's tfsm/states/events have changed.
            show_roi (bool): If set to True, only render states that are active and/or can be reached from
                the current state.
        Returns: AGraph (pygraphviz) or Digraph (graphviz) graph instance that can be drawn.
        """
        _LOGGER.info("Returning graph of the first model. In future releases, this method will return a combined graph of all models.")
        return self._get_graph(self.models[0], title, force_new, show_roi)

    def add_model(self, model: Any, initial: Any | None = None) -> None:  # type: ignore[override]
        """Register a model with the state machine and initialize its graph.

        This override adds graph initialization to the parent class behavior.
        """
        models = listify(model)
        super().add_model(models, initial)
        for mod in models:
            mod = self if mod is self.self_literal else mod
            if hasattr(mod, "get_graph"):
                raise AttributeError("Model already has a get_graph attribute. Graph retrieval cannot be bound.")
            mod.get_graph = partial(self._get_graph, mod)
            _ = mod.get_graph(title=self.title, force_new=True)  # initialises graph

    def add_states(
        self,
        states: Any,
        on_enter: Any | None = None,
        on_exit: Any | None = None,
        ignore_invalid_triggers: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """Calls the base method and regenerates all models' graphs."""
        super().add_states(states, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs)
        for model in self.models:
            model.get_graph(force_new=True)

    def add_transition(
        self,
        trigger: Any,
        source: Any,
        dest: Any,
        conditions: Any | None = None,
        unless: Any | None = None,
        before: Any | None = None,
        after: Any | None = None,
        prepare: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """Calls the base method and regenerates all models's graphs."""
        super().add_transition(
            trigger, source, dest, conditions=conditions, unless=unless, before=before, after=after, prepare=prepare, **kwargs
        )
        for model in self.models:
            model.get_graph(force_new=True)

    def remove_transition(self, trigger: Any, source: Any = "*", dest: Any = "*") -> None:
        super().remove_transition(trigger, source, dest)
        # update all model graphs since some tfsm might be gone
        for model in self.models:
            _ = model.get_graph(force_new=True)


class NestedGraphTransition(TransitionGraphSupport, NestedTransition):
    """
    A transition type to be used with (subclasses of) `HierarchicalGraphMachine` and
    `LockedHierarchicalGraphMachine`.
    """


class HierarchicalGraphMachine(GraphMachine, HierarchicalMarkupMachine):
    """
    A hierarchical state machine with graph support.
    """

    transition_cls = NestedGraphTransition
