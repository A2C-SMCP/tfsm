# -*- coding: utf-8 -*-
"""
tfsm.extensions.nesting
------------------------------

Implements a hierarchical state machine based on tfsm.core.Machine. Supports nested states, parallel states
and the composition of multiple hierarchical state machines.
"""

import copy
import inspect
import logging
from collections import OrderedDict
from enum import Enum, EnumMeta
from functools import partial, reduce
from typing import Any, Optional, Union

from ..core import Callback, CallbackList, Event, EventData, Machine, MachineError, State, StateName, Transition, listify

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())


# converts a hierarchical tree into a list of current states
def _build_state_list(state_tree: dict[str, Any], separator: str, prefix: list[str] | None = None) -> str | list[Any]:
    prefix = prefix or []
    res: list[Any] = []
    for key, value in state_tree.items():
        if value:
            res.append(_build_state_list(value, separator, prefix=prefix + [key]))
        else:
            res.append(separator.join(prefix + [key]))
    return res if len(res) > 1 else res[0]


def resolve_order(state_tree: dict[str, Any]) -> Any:  # reversed[List[List[str]]]
    """Converts a (model) state tree into a list of state paths. States are ordered in the way in which states
    should be visited to process the event correctly (Breadth-first). This makes sure that ALL children are evaluated
    before parents in parallel states.
    Args:
        state_tree (dict): A dictionary representation of the model's state.
    Returns:
        list of lists of str representing the order of states to be processed.
    """
    queue: list[tuple[list[str], dict[str, Any]]] = []
    res: list[list[str]] = []
    prefix: list[str] = []
    while True:
        for state_name in reversed(list(state_tree.keys())):
            scope = prefix + [state_name]
            res.append(scope)
            if state_tree[state_name]:
                queue.append((scope, state_tree[state_name]))
        if not queue:
            break
        prefix, state_tree = queue.pop(0)
    return list(reversed(res))


class FunctionWrapper:
    """A wrapper to enable tfsm' convenience function to_<state> for nested states.
    This allows to call model.to_A.s1.C() in case a custom separator has been chosen."""

    def __init__(self, func: Callback) -> None:
        """
        Args:
            func: Function to be called at the end of the path.
            path: If path is an empty string, assign function
        """
        self._func: Callback = func

    def add(self, func: Callback, path: list[str]) -> None:
        """Assigns a `FunctionWrapper` as an attribute named like the next segment of the substates
            path.
        Args:
            func (callable): Function to be called at the end of the path.
            path (list of strings): Remaining segment of the substate path.
        """
        if not path:
            self._func = func
        else:
            name = path[0]
            if name[0].isdigit():
                name = "s" + name
            if hasattr(self, name):
                getattr(self, name).add(func, path[1:])
            else:
                assert not path[1:], "nested path should be empty"
                setattr(self, name, FunctionWrapper(func))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)


class NestedEvent(Event):
    """An event type to work with nested states.
    This subclass is NOT compatible with simple Machine instances.
    """

    def trigger(self, model: Any, *args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("NestedEvent.trigger must not be called directly. Call Machine.trigger_event instead.")

    def trigger_nested(self, event_data: "NestedEventData") -> bool:
        """Executes all tfsm that match the current state,
        halting as soon as one successfully completes.
        It is up to the machine's configuration of the Event whether processing happens queued (sequentially) or
        whether further Events are processed as they occur. NOTE: This should only
        be called by HierarchicalMachine instances.
        Args:
            event_data (NestedEventData): The currently processed event
        Returns: boolean indicating whether or not a transition was
            successfully executed (True if successful, False if not).
        """
        machine = event_data.machine
        model = event_data.model
        state_tree = machine.build_state_tree(getattr(model, machine.model_attribute), machine.state_cls.separator)  # type: ignore[attr-defined]
        state_tree = reduce(dict.get, machine.get_global_name(join=False), state_tree)
        ordered_states = resolve_order(state_tree)
        done = set()
        event_data.event = self
        for state_path in ordered_states:
            state_name = machine.state_cls.separator.join(state_path)  # type: ignore[attr-defined]
            if state_name not in done and state_name in self.transitions:
                event_data.state = machine.get_state(state_name)
                event_data.source_name = state_name
                event_data.source_path = copy.copy(state_path)
                self._process(event_data)
                if event_data.result:
                    elems = state_path
                    while elems:
                        done.add(machine.state_cls.separator.join(elems))  # type: ignore[attr-defined]
                        elems.pop()
        return event_data.result

    def _process(self, event_data: EventData) -> None:
        machine = event_data.machine
        machine.callbacks(event_data.machine.prepare_event, event_data)
        _LOGGER.debug("%sExecuted machine preparation callbacks before conditions.", machine.name)
        for trans in self.transitions[event_data.source_name]:  # type: ignore[attr-defined]
            event_data.transition = trans
            event_data.result = trans.execute(event_data)
            if event_data.result:
                break


class NestedEventData(EventData):
    """Collection of relevant data related to the ongoing nested transition attempt."""

    __slots__ = [
        "state",
        "event",
        "machine",
        "model",
        "args",
        "kwargs",
        "transition",
        "error",
        "result",
        "source_path",
        "source_name",
        "scope",
    ]

    def __init__(
        self,
        state: Optional["NestedState"],
        event: Optional["NestedEvent"],
        machine: "HierarchicalMachine",
        model: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        super().__init__(state, event, machine, model, args, kwargs)  # type: ignore[arg-type]
        self.source_path: list[str] | None = None
        self.source_name: str | None = None
        self.scope: list[str] | None = None  # Dynamic attribute


class NestedState(State):
    """A state which allows substates.
    Attributes:
        states (OrderedDict): A list of substates of the current state.
        events (dict): A list of events defined for the nested state.
        initial (list, str, NestedState or Enum): (Name of a) child or list of children that should be entered when the
        state is entered.
    """

    __slots__ = [
        "_name",
        "final",
        "ignore_invalid_triggers",
        "on_enter",
        "on_exit",
        "initial",
        "events",
        "states",
        "on_final",
        "_scope",
        "_pocket",
    ]

    separator = "_"
    """ Separator between the names of parent and child states. In case '_' is required for
        naming state, this value can be set to other values such as '.' or even unicode characters
        such as '↦' (limited to Python 3 though).
    """

    dynamic_methods = State.dynamic_methods + ["on_final"]

    def __init__(
        self,
        name: StateName,
        on_enter: str | CallbackList | None = None,
        on_exit: str | CallbackList | None = None,
        ignore_invalid_triggers: bool | None = None,
        final: bool = False,
        initial: str | StateName | list[str | StateName] | None = None,
        on_final: str | CallbackList | None = None,
    ) -> None:
        super().__init__(name=name, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, final=final)
        self.initial: str | StateName | list[str | StateName] | None = initial
        self.events: dict[str, Any] = {}  # Dynamic attribute added to NestedState
        self.states: OrderedDict[str, NestedState] = OrderedDict()  # Dynamic attribute added to NestedState
        self.on_final: CallbackList = listify(on_final)  # type: ignore[assignment]
        self._scope: list[str] = []

    def add_substate(self, state: "NestedState") -> None:
        """Adds a state as a substate.
        Args:
            state (NestedState): State to add to the current state.
        """
        self.add_substates(state)

    def add_substates(self, states: Union["NestedState", list["NestedState"]]) -> None:
        """Adds a list of states to the current state.
        Args:
            states (list): List of state to add to the current state.
        """
        for state in listify(states):
            self.states[state.name] = state

    def scoped_enter(self, event_data: "NestedEventData", scope: list[str] | None = None) -> None:
        """Enters a state with the provided scope.
        Args:
            event_data (NestedEventData): The currently processed event.
            scope (list(str)): Names of the state's parents starting with the top most parent.
        """
        self._scope = scope or []
        try:
            self.enter(event_data)
        finally:
            self._scope = []

    def scoped_exit(self, event_data: "NestedEventData", scope: list[str] | None = None) -> None:
        """Exits a state with the provided scope.
        Args:
            event_data (NestedEventData): The currently processed event.
            scope (list(str)): Names of the state's parents starting with the top most parent.
        """
        self._scope = scope or []
        try:
            self.exit(event_data)
        finally:
            self._scope = []

    @property
    def name(self) -> str:
        return self.separator.join(self._scope + [super().name])


class NestedTransition(Transition):
    """A transition which handles entering and leaving nested states."""

    __slots__ = ["source", "dest", "prepare", "before", "after", "conditions"]

    def _resolve_transition(
        self, event_data: "NestedEventData"
    ) -> tuple[dict[str, Any], Any, Any]:  # List[Callback], List[Callback] but partial makes it complex
        # Convert dest to string if it's an Enum
        dest_str = self.dest if isinstance(self.dest, str) else str(self.dest)
        dst_name_path = dest_str.split(event_data.machine.state_cls.separator)  # type: ignore[attr-defined]
        _ = event_data.machine.get_state(dst_name_path[0] if len(dst_name_path) == 1 else dst_name_path)  # type: ignore[arg-type]
        state_tree = event_data.machine.build_state_tree(
            listify(getattr(event_data.model, event_data.machine.model_attribute)),
            event_data.machine.state_cls.separator,  # type: ignore[attr-defined]
        )

        scope = event_data.machine.get_global_name(join=False)
        tmp_tree = state_tree.get(dst_name_path[0], None)
        root = []
        while tmp_tree is not None:
            root.append(dst_name_path.pop(0))
            tmp_tree = tmp_tree.get(dst_name_path[0], None) if len(dst_name_path) > 0 else None

        # when destination is empty this means we are already in the state we want to enter
        # we deal with a reflexive transition here or a sibling state that has already been entered
        # as internal tfsm have been already dealt with
        # the 'root' of src and dest will be set to the parent and dst (and src) substate will be set as destination
        if not dst_name_path:
            dst_name_path = [root.pop()]

        scoped_tree = reduce(dict.get, scope + root, state_tree)

        # if our scope is a parallel state we need to narrow down the exit scope to the targeted sibling
        if len(scoped_tree) > 1:
            exit_scope = {dst_name_path[0]: scoped_tree.get(dst_name_path[0])}
        else:
            exit_scope = scoped_tree

        exit_partials = [
            partial(
                event_data.machine.get_state(root + state_name).scoped_exit,  # type: ignore[attr-defined]
                event_data,
                scope + root + state_name[:-1],
            )
            for state_name in resolve_order(exit_scope)
        ]

        new_states, enter_partials = self._enter_nested(root, dst_name_path, scope + root, event_data)

        # we reset/clear the whole branch if it is scoped, otherwise only reset the sibling
        if exit_scope == scoped_tree:
            scoped_tree.clear()
        for new_key, value in new_states.items():
            scoped_tree[new_key] = value
            break

        return state_tree, exit_partials, enter_partials

    def _change_state(self, event_data: EventData) -> None:
        state_tree, exit_partials, enter_partials = self._resolve_transition(event_data)  # type: ignore[arg-type]
        for func in exit_partials:
            func()
        self._update_model(event_data, state_tree)  # type: ignore[arg-type]
        for func in enter_partials:
            func()
        with event_data.machine():  # type: ignore[operator]  # HierarchicalMachine is callable
            on_final_cbs, _ = self._final_check(event_data, state_tree, enter_partials)  # type: ignore[arg-type]
            for on_final_cb in on_final_cbs:
                on_final_cb()

    def _final_check(
        self, event_data: "NestedEventData", state_tree: dict[str, Any], enter_partials: list[Any]
    ) -> tuple[list[Callback], bool]:
        on_final_cbs = []
        is_final = False
        # processes states with children
        if state_tree:
            all_children_final = True
            for child_cbs, child_final in (self._final_check_nested(state, event_data, state_tree, enter_partials) for state in state_tree):
                # if one child is not considered final, processing can stop
                if not child_final:
                    all_children_final = False
                    # if one child has recently transitioned to a final state, we need to update all parents
                on_final_cbs.extend(child_cbs)
            # if and only if all other children are also in a final state and a child has recently reached a final
            # state OR the scoped state has just been entered, trigger callbacks
            if all_children_final:
                if on_final_cbs or any(event_data.machine.scoped.scoped_enter == part.func for part in enter_partials):
                    on_final_cbs.append(partial(event_data.machine.callbacks, event_data.machine.scoped.on_final, event_data))
                is_final = True
        # if a state is a leaf state OR has children not in a final state
        elif getattr(event_data.machine.scoped, "final", False):
            # if the state itself is considered final and has recently been entered trigger callbacks
            # thus, a state with non-final children may still trigger callbacks if itself is considered final
            if any(event_data.machine.scoped.scoped_enter == part.func for part in enter_partials):
                on_final_cbs.append(partial(event_data.machine.callbacks, event_data.machine.scoped.on_final, event_data))
            is_final = True
        return on_final_cbs, is_final

    def _final_check_nested(
        self, state: str, event_data: "NestedEventData", state_tree: dict[str, Any], enter_partials: list[Any]
    ) -> tuple[list[Callback], bool]:
        with event_data.machine(state):  # type: ignore[operator]  # HierarchicalMachine is callable
            return self._final_check(event_data, state_tree[state], enter_partials)

    def _enter_nested(
        self, root: list[str], dest: list[str], prefix_path: list[str], event_data: "NestedEventData"
    ) -> tuple[dict[str, Any], list[Callback]]:
        if root:
            state_name = root.pop(0)
            with event_data.machine(state_name):  # type: ignore[operator]
                return self._enter_nested(root, dest, prefix_path, event_data)
        elif dest:
            new_states: OrderedDict[str, OrderedDict[str, Any]] = OrderedDict()
            state_name = dest.pop(0)
            with event_data.machine(state_name):  # type: ignore[operator]
                new_states[state_name], new_enter = self._enter_nested([], dest, prefix_path + [state_name], event_data)  # type: ignore[assignment]
                enter_partials = [partial(event_data.machine.scoped.scoped_enter, event_data, prefix_path)] + new_enter
            return new_states, enter_partials
        elif event_data.machine.scoped.initial:
            new_states_2: OrderedDict[str, OrderedDict[str, Any]] = OrderedDict()
            enter_partials = []
            queue: list[tuple[Any, OrderedDict[str, NestedState], dict[str, Any], list[str]]] = []
            prefix = prefix_path
            scoped_tree: OrderedDict[str, OrderedDict[str, Any]] = new_states_2
            initial_names = [i.name if hasattr(i, "name") else i for i in listify(event_data.machine.scoped.initial)]
            initial_states = [event_data.machine.scoped.states[n] for n in initial_names]
            while True:
                event_data.scope = prefix
                for state in initial_states:
                    enter_partials.append(partial(state.scoped_enter, event_data, prefix))
                    scoped_tree[state.name] = OrderedDict()
                    if state.initial:
                        queue.append((
                            scoped_tree[state.name],
                            prefix + [state.name],  # type: ignore[arg-type]
                            [state.states[i.name] if hasattr(i, "name") else state.states[i] for i in listify(state.initial)],
                        ))
                if not queue:
                    break
                scoped_tree, prefix, initial_states = queue.pop(0)  # type: ignore[misc]
            return new_states_2, enter_partials
        else:
            return {}, []

    @staticmethod
    def _update_model(event_data: "NestedEventData", tree: dict[str, Any]) -> None:
        model_states = _build_state_list(tree, event_data.machine.state_cls.separator)  # type: ignore[attr-defined]
        with event_data.machine():  # type: ignore[operator]  # HierarchicalMachine is callable
            event_data.machine.set_state(model_states, event_data.model)  # type: ignore[arg-type]
            states = event_data.machine.get_states(listify(model_states))
            event_data.state = states[0] if len(states) == 1 else states

    # Prevent deep copying of callback lists since these include either references to callable or
    # strings. Deep copying a method reference would lead to the creation of an entire new (model) object
    # (see https://github.com/pytransitions/transitions/issues/248)
    # Note: When conditions are handled like other dynamic callbacks the key == "conditions" clause can be removed
    def __deepcopy__(self, memo: dict[int, Any]) -> "NestedTransition":
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        # Iterate over __slots__ instead of __dict__ since NestedTransition uses __slots__
        for slot in cls.__slots__:
            if hasattr(self, slot):
                value = getattr(self, slot)
                if slot in cls.dynamic_methods or slot == "conditions":
                    setattr(result, slot, copy.copy(value))
                else:
                    setattr(result, slot, copy.deepcopy(value, memo))
        return result


class HierarchicalMachine(Machine):
    """Extends tfsm.core.Machine by capabilities to handle nested states.
    A hierarchical machine REQUIRES NestedStates, NestedEvent and NestedTransitions
    (or any subclass of it) to operate.
    """

    state_cls = NestedState
    transition_cls = NestedTransition
    event_cls = NestedEvent

    def __init__(
        self,
        model: Any = Machine.self_literal,
        states: Any | None = None,
        initial: str | StateName = "initial",
        transitions: Any | None = None,
        send_event: bool = False,
        auto_transitions: bool = True,
        ordered_transitions: bool = False,
        ignore_invalid_triggers: bool | None = None,
        before_state_change: str | CallbackList | None = None,
        after_state_change: str | CallbackList | None = None,
        name: str | None = None,
        queued: bool = False,
        prepare_event: str | CallbackList | None = None,
        finalize_event: str | CallbackList | None = None,
        model_attribute: str = "state",
        model_override: bool = False,
        on_exception: str | CallbackList | None = None,
        on_final: str | CallbackList | None = None,
        **kwargs: Any,
    ) -> None:
        assert issubclass(self.state_cls, NestedState)
        assert issubclass(self.event_cls, NestedEvent)
        assert issubclass(self.transition_cls, NestedTransition)
        self._stack: list[tuple[Any, OrderedDict[str, NestedState], dict[str, Any], list[str]]] = []
        self.prefix_path: list[str] = []
        self.scoped: HierarchicalMachine = self
        self._next_scope: tuple[Any, OrderedDict[str, NestedState], dict[str, Any], list[str]] | None = None
        super().__init__(
            model=model,
            states=states,
            initial=initial,
            transitions=transitions,
            send_event=send_event,
            auto_transitions=auto_transitions,
            ordered_transitions=ordered_transitions,
            ignore_invalid_triggers=ignore_invalid_triggers,
            before_state_change=before_state_change,  # type: ignore[arg-type]
            after_state_change=after_state_change,  # type: ignore[arg-type]
            name=name,
            queued=queued,
            prepare_event=prepare_event,  # type: ignore[arg-type]
            finalize_event=finalize_event,  # type: ignore[arg-type]
            model_attribute=model_attribute,
            model_override=model_override,
            on_exception=on_exception,  # type: ignore[arg-type]
            on_final=on_final,  # type: ignore[arg-type]
            **kwargs,
        )

    def __call__(self, to_scope: Union[str, Enum, "NestedState"] | None = None) -> "HierarchicalMachine":
        if isinstance(to_scope, Enum):
            state = self.states[to_scope.name]
            # Dynamic attributes added to NestedState
            to_scope = (state, state.states, state.events, self.prefix_path + [to_scope.name])  # type: ignore[attr-defined, assignment]
        elif isinstance(to_scope, str):
            state_name = to_scope.split(self.state_cls.separator)[0]
            state = self.states[state_name]
            # Dynamic attributes added to NestedState
            to_scope = (state, state.states, state.events, self.prefix_path + [state_name])  # type: ignore[attr-defined, assignment]
        elif to_scope is None:
            if self._stack:
                to_scope = self._stack[0]  # type: ignore[assignment]
            else:
                to_scope = (self, self.states, self.events, [])  # type: ignore[assignment]
        self._next_scope: tuple[Any, OrderedDict[str, NestedState], dict[str, Any], list[str]] | None = to_scope  # type: ignore[no-redef, assignment]

        return self

    def __enter__(self) -> None:
        self._stack.append((self.scoped, self.states, self.events, self.prefix_path))  # type: ignore[arg-type]
        if self._next_scope is not None:
            self.scoped, self.states, self.events, self.prefix_path = self._next_scope  # type: ignore[assignment]
        else:
            raise RuntimeError("No scope set for context manager")
        self._next_scope = None

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.scoped, self.states, self.events, self.prefix_path = self._stack.pop()  # type: ignore[assignment]

    def add_model(self, model: Any, initial: Any | None = None) -> None:  # type: ignore[override]
        """Extends tfsm.core.Machine.add_model by applying a custom 'to' function to
        the added model.
        """
        models = [self if mod is self.self_literal else mod for mod in listify(model)]
        super().add_model(models, initial=initial)
        initial_name = getattr(models[0], self.model_attribute)
        if hasattr(initial_name, "name"):
            initial_name = initial_name.name
        # initial states set by add_model or machine might contain initial states themselves.
        if isinstance(initial_name, str):
            initial_states = self._resolve_initial(models, initial_name.split(self.state_cls.separator))
        # when initial is set to a (parallel) state, we accept it as it is
        else:
            initial_states = initial_name
        for mod in models:
            self.set_state(initial_states, mod)
            if hasattr(mod, "to"):
                _LOGGER.warning("%sModel already has a 'to'-method. It will NOT be overwritten by NestedMachine", self.name)
            else:
                to_func = partial(self.to_state, mod)
                mod.to = to_func  # type: ignore[union-attr]

    @property  # type: ignore[override]
    def initial(self) -> str | StateName | list[Any] | None:
        # TODO: Architectural issue - return type incompatible with parent class Machine
        """Return the initial state."""
        return self._initial

    @initial.setter
    def initial(self, value: Any) -> None:
        # TODO: Architectural issue - property type incompatible with parent class Machine
        self._initial = self._recursive_initial(value)  # type: ignore[assignment]

    def add_ordered_transitions(
        self,
        states: Any | None = None,
        trigger: str = "next_state",
        loop: bool = True,
        loop_includes_initial: bool = True,
        conditions: Any | None = None,
        unless: Any | None = None,
        before: Any | None = None,
        after: Any | None = None,
        prepare: Any | None = None,
        **kwargs: Any,
    ) -> None:
        if states is None:
            states = self.get_nested_state_names()
        super().add_ordered_transitions(
            states=states,  # type: ignore[arg-type]
            trigger=trigger,
            loop=loop,
            loop_includes_initial=loop_includes_initial,
            conditions=conditions,
            unless=unless,
            before=before,
            after=after,
            prepare=prepare,
            **kwargs,
        )

    def add_states(
        self,
        states: Any,
        on_enter: str | CallbackList | None = None,
        on_exit: str | CallbackList | None = None,
        ignore_invalid_triggers: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Add new nested state(s).
        Args:
            states (list, str, dict, Enum, NestedState or HierarchicalMachine): a list, a NestedState instance, the
                name of a new state, an enumeration (member) or a dict with keywords to pass on to the
                NestedState initializer. If a list, each element can be a string, dict, NestedState or
                enumeration member.
            on_enter (str or list): callbacks to trigger when the state is
                entered. Only valid if first argument is string.
            on_exit (str or list): callbacks to trigger when the state is
                exited. Only valid if first argument is string.
            ignore_invalid_triggers: when True, any calls to trigger methods
                that are not valid for the present state (e.g., calling an
                a_to_b() trigger when the current state is c) will be silently
                ignored rather than raising an invalid transition exception.
                Note that this argument takes precedence over the same
                argument defined at the Machine level, and is in turn
                overridden by any ignore_invalid_triggers explicitly
                passed in an individual state's initialization arguments.
            **kwargs additional keyword arguments used by state mixins.
        """
        remap = kwargs.pop("remap", None)
        ignore = self.ignore_invalid_triggers if ignore_invalid_triggers is None else ignore_invalid_triggers

        for state in listify(states):
            if isinstance(state, Enum):
                if isinstance(state.value, EnumMeta):
                    state = {"name": state, "children": state.value}
                elif isinstance(state.value, dict):
                    state = dict(name=state, **state.value)
            if isinstance(state, str):
                self._add_string_state(state, on_enter, on_exit, ignore, remap, **kwargs)
            elif isinstance(state, Enum):
                self._add_enum_state(state, on_enter, on_exit, ignore, remap, **kwargs)
            elif isinstance(state, dict):
                self._add_dict_state(state, ignore, remap, **kwargs)
            elif isinstance(state, NestedState):
                if state.name in self.states:
                    raise ValueError(f"State {state.name} cannot be added since it already exists.")
                self.states[state.name] = state
                self._init_state(state)
            elif isinstance(state, HierarchicalMachine):
                self._add_machine_states(state, remap)
            elif isinstance(state, State) and not isinstance(state, NestedState):
                raise ValueError("A passed state object must derive from NestedState! A default State object is not sufficient")
            else:
                raise ValueError(f"Cannot add state of type {type(state).__name__}. ")

    def add_transition(
        self,
        trigger: str,
        source: Any,
        dest: Any,
        conditions: Any | None = None,
        unless: Any | None = None,
        before: Any | None = None,
        after: Any | None = None,
        prepare: Any | None = None,
        **kwargs: Any,
    ) -> None:
        if source == self.wildcard_all and dest == self.wildcard_same:
            source = self.get_nested_state_names()
        else:
            if source != self.wildcard_all:
                source = [
                    self.state_cls.separator.join(self._get_enum_path(s)) if isinstance(s, Enum) else s  # type: ignore[arg-type]
                    for s in listify(source)
                ]
            if dest != self.wildcard_same:
                dest = self.state_cls.separator.join(self._get_enum_path(dest)) if isinstance(dest, Enum) else dest  # type: ignore[arg-type]
        super().add_transition(trigger, source, dest, conditions, unless, before, after, prepare, **kwargs)

    def get_global_name(self, state: Union[str, Enum, "NestedState"] | None = None, join: bool = True) -> str | list[str]:
        """Returns the name of the passed state in context of the current prefix/scope.
        Args:
            state (str, Enum or NestedState): The state to be analyzed.
            join (bool): Whether this method should join the path elements or not
        Returns:
            str or list(str) of the global state name
        """
        domains = copy.copy(self.prefix_path)
        if state:
            state_name = state.name if hasattr(state, "name") else state
            if state_name in self.states:
                domains.append(state_name)
            else:
                raise ValueError(f"State '{state}' not found in local states.")
        return self.state_cls.separator.join(domains) if join else domains

    def get_nested_state_names(self) -> list[str]:
        """Returns a list of global names of all states of a machine.
        Returns:
            list(str) of global state names.
        """
        ordered_states: list[str] = []
        for state in self.states.values():
            ordered_states.append(self.get_global_name(state))  # type: ignore[arg-type]
            with self(state.name):
                ordered_states.extend(self.get_nested_state_names())
        return ordered_states

    def get_nested_transitions(self, trigger: str = "", src_path: list[str] | None = None, dest_path: list[str] | None = None) -> list[Any]:
        """Retrieves a list of all tfsm matching the passed requirements.
        Args:
            trigger (str): If set, return only tfsm related to this trigger.
            src_path (list(str)): If set, return only tfsm with this source state.
            dest_path (list(str)): If set, return only tfsm with this destination.

        Returns:
            list(NestedTransitions) of valid tfsm.
        """
        if src_path and dest_path:
            src = self.state_cls.separator.join(src_path)
            dest = self.state_cls.separator.join(dest_path)
            transitions = super().get_transitions(trigger, src, dest)
            if len(src_path) > 1 and len(dest_path) > 1:
                with self(src_path[0]):
                    transitions.extend(self.get_nested_transitions(trigger, src_path[1:], dest_path[1:]))
        elif src_path:
            src = self.state_cls.separator.join(src_path)
            transitions = super().get_transitions(trigger, src, "*")
            if len(src_path) > 1:
                with self(src_path[0]):
                    transitions.extend(self.get_nested_transitions(trigger, src_path[1:], None))
        elif dest_path:
            dest = self.state_cls.separator.join(dest_path)
            transitions = super().get_transitions(trigger, "*", dest)
            if len(dest_path) > 1:
                for state_name in self.states:
                    with self(state_name):
                        transitions.extend(self.get_nested_transitions(trigger, None, dest_path[1:]))
        else:
            transitions = super().get_transitions(trigger, "*", "*")
            for state_name in self.states:
                with self(state_name):
                    transitions.extend(self.get_nested_transitions(trigger, None, None))
        return transitions

    def get_nested_triggers(self, src_path: list[str] | None = None) -> list[str]:
        """Retrieves a list of valid triggers.
        Args:
            src_path (list(str)): A list representation of the source state's name.
        Returns:
            list(str) of valid trigger names.
        """
        if src_path:
            triggers = super().get_triggers(self.state_cls.separator.join(src_path))
            if len(src_path) > 1 and src_path[0] in self.states:
                with self(src_path[0]):
                    triggers.extend(self.get_nested_triggers(src_path[1:]))
        else:
            triggers = list(self.events.keys())
            for state_name in self.states:
                with self(state_name):
                    triggers.extend(self.get_nested_triggers())
        return triggers

    def get_state(self, state: str | Enum | list[str], hint: list[str] | None = None) -> "NestedState":
        """Return the State instance with the passed name.
        Args:
            state (str, Enum or list(str)): A state name, enum or state path
            hint (list(str)): A state path to check for the state in question
        Returns:
            NestedState that belongs to the passed str (list) or Enum.
        """
        if isinstance(state, Enum):
            state = self._get_enum_path(state)  # type: ignore[assignment]
        elif isinstance(state, str):
            state = state.split(self.state_cls.separator)
        if not hint:
            state = copy.copy(state)
            hint = copy.copy(state)  # type: ignore[assignment]  # type: ignore[assignment]
        if len(state) > 1:  # type: ignore[arg-type]
            child = state.pop(0)  # type: ignore[union-attr]
            try:
                with self(child):
                    return self.get_state(state, hint)
            except (KeyError, ValueError):
                try:
                    with self():
                        state_obj: HierarchicalMachine | NestedState = self
                        for elem in hint:  # type: ignore[union-attr]
                            state_obj = state_obj.states[elem]  # type: ignore[assignment]
                        return state_obj  # type: ignore[return-value]
                except KeyError:
                    raise ValueError(
                        "State '%s' is not a registered state." % self.state_cls.separator.join(hint)  # type: ignore[arg-type]
                    )  # from KeyError
        elif state[0] not in self.states:  # type: ignore[index]
            raise ValueError("State '%s' is not a registered state." % state)
        return self.states[state[0]]  # type: ignore[index, return-value]

    def get_states(self, states: list[str | Enum | list[Any]] | str | Enum) -> Any:
        """Retrieves a list of NestedStates.
        Args:
            states (str, Enum or list of str or Enum): Names/values of the states to retrieve.
        Returns:
            list(NestedStates) belonging to the passed identifiers.
        """
        res = []
        for state in states:  # type: ignore[union-attr]
            if isinstance(state, list):
                res.append(self.get_states(state))
            else:
                res.append(self.get_state(state))
        return res

    def get_transitions(
        self, trigger: str = "", source: str | StateName = "*", dest: str | StateName = "*", delegate: bool = False
    ) -> list[Any]:
        """Return the tfsm from the Machine.
        Args:
            trigger (str): Trigger name of the transition.
            source (str, State or Enum): Limits list to tfsm from a certain state.
            dest (str, State or Enum): Limits list to tfsm to a certain state.
            delegate (Optional[bool]): If True, consider delegations to parents of source
        Returns:
            list(NestedTransitions): All tfsm matching the request.
        """
        with self():
            source_path = (
                []
                if source == "*"
                else self._get_enum_path(source)
                if isinstance(source, Enum)
                else source.split(self.state_cls.separator)
                if isinstance(source, str)
                else self._get_state_path(source)
            )
            dest_path = (
                []
                if dest == "*"
                else self._get_enum_path(dest)
                if isinstance(dest, Enum)
                else dest.split(self.state_cls.separator)
                if isinstance(dest, str)
                else self._get_state_path(dest)
            )
            matches = self.get_nested_transitions(trigger, source_path, dest_path)
            # only consider delegations when source_path contains a nested state (len > 1)
            if delegate is False or len(source_path) < 2:  # type: ignore[arg-type]
                return matches
            source_path.pop()  # type: ignore[union-attr]
            while source_path:
                matches.extend(self.get_nested_transitions(trigger, src_path=source_path, dest_path=dest_path))
                source_path.pop()
            return matches

    def _remove_nested_transitions(self, trigger: str, src_path: list[str], dest_path: list[str]) -> None:
        """Remove tfsm from nested states.
        Args:
            trigger (str): Trigger name of the transition to be removed.
            src_path (list(str)): If empty, all tfsm that match dest_path and trigger will be removed.
            dest_path (list(str)): If empty, all tfsm that match src_path and trigger will be removed.
        """
        cur_src = self.state_cls.separator.join(src_path)
        cur_dst = self.state_cls.separator.join(dest_path)
        if trigger in self.scoped.events:
            evt = self.scoped.events[trigger]
            for src, transitions in evt.transitions.items():
                evt.transitions[src] = [
                    trans for trans in transitions if (src_path and trans.source != cur_src) or (cur_dst and trans.dest != cur_dst)
                ]
        for state_name in self.scoped.states:
            with self(state_name):
                if state_name in [cur_src, cur_dst]:
                    continue
                self._remove_nested_transitions(
                    trigger,
                    src_path if not src_path or state_name != src_path[0] else src_path[1:],
                    dest_path if not dest_path or state_name != dest_path[0] else dest_path[1:],
                )

    def remove_transition(self, trigger: str, source: str | StateName = "*", dest: str | StateName = "*") -> None:
        """Removes tfsm matching the passed criteria.
        Args:
            trigger (str): Trigger name of the transition.
            source (str, State or Enum): Limits list to tfsm from a certain state.
            dest (str, State or Enum): Limits list to tfsm to a certain state.
        """
        with self():
            source_path = (
                []
                if source == "*"
                else self._get_enum_path(source)
                if isinstance(source, Enum)
                else source.split(self.state_cls.separator)
                if isinstance(source, str)
                else self._get_state_path(source)
            )
            dest_path = (
                []
                if dest == "*"
                else self._get_enum_path(dest)
                if isinstance(dest, Enum)
                else dest.split(self.state_cls.separator)
                if isinstance(dest, str)
                else self._get_state_path(dest)
            )
            self._remove_nested_transitions(trigger, source_path or [], dest_path or [])

        # remove trigger from models if no transition is left for trigger
        if not self.get_transitions(trigger):
            for model in self.models:
                delattr(model, trigger)

    def _can_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        state_tree = self.build_state_tree(getattr(model, self.model_attribute), self.state_cls.separator)
        ordered_states = resolve_order(state_tree)
        with self():
            return any(self._can_trigger_nested(model, trigger, state_path, *args, **kwargs) for state_path in ordered_states)

    def _can_trigger_nested(self, model: Any, trigger: str, path: list[str], *args: Any, **kwargs: Any) -> bool:
        if trigger in self.events:
            source_path = copy.copy(path)
            while source_path:
                event_data = EventData(self.get_state(source_path), Event(name=trigger, machine=self), self, model, args, kwargs)
                state_name = self.state_cls.separator.join(source_path)
                for transition in self.events[trigger].transitions.get(state_name, []):
                    try:
                        _ = self.get_state(transition.dest) if transition.dest is not None else transition.source
                    except ValueError:
                        continue
                    event_data.transition = transition
                    try:
                        self.callbacks(self.prepare_event, event_data)
                        self.callbacks(transition.prepare, event_data)
                        if all(c.check(event_data) for c in transition.conditions):
                            return True
                    except BaseException as err:  # pylint: disable=broad-except
                        event_data.error = err  # type: ignore[assignment]
                        if self.on_exception:
                            self.callbacks(self.on_exception, event_data)
                        else:
                            raise
                source_path.pop(-1)
        if path:
            with self(path.pop(0)):
                return self._can_trigger_nested(model, trigger, path, *args, **kwargs)
        return False

    def get_triggers(self, *args: Any) -> list[str]:
        """Extends tfsm.core.Machine.get_triggers to also include parent state triggers."""
        triggers = []
        with self():
            for state in args:
                state_name = state.name if hasattr(state, "name") else state
                state_path = state_name.split(self.state_cls.separator)
                if len(state_path) > 1:  # we only need to check substates when 'state_name' refers to a substate
                    with self(state_path[0]):
                        triggers.extend(self.get_nested_triggers(state_path[1:]))
                while state_path:  # check all valid tfsm for parent states
                    triggers.extend(super().get_triggers(self.state_cls.separator.join(state_path)))
                    state_path.pop()
        return triggers

    def has_trigger(self, trigger: str, state: Union["NestedState", "HierarchicalMachine"] | None = None) -> bool:
        """Check whether an event/trigger is known to the machine
        Args:
            trigger (str): Event/trigger name
            state (optional[NestedState]): Limits the recursive search to this state and its children
        Returns:
            bool: True if event is known and False otherwise
        """

        state = state or self
        # Dynamic attributes on state
        return trigger in state.events or any(self.has_trigger(trigger, sta) for sta in state.states.values())  # type: ignore[arg-type]

    def is_state(self, state: str | Enum, model: Any, allow_substates: bool = False) -> bool:
        tree = self.build_state_tree(
            listify(getattr(model, self.model_attribute)),  # type: ignore[arg-type]
            self.state_cls.separator,
        )

        path = self._get_enum_path(state) if isinstance(state, Enum) else state.split(self.state_cls.separator)
        for elem in path:  # type: ignore[union-attr]
            if elem not in tree:
                return False
            tree = tree[elem]
        return len(tree) == 0 or allow_substates

    def on_enter(self, state_name: str, callback: str | Callback) -> None:
        """Helper function to add callbacks to states in case a custom state separator is used.
        Args:
            state_name (str): Name of the state
            callback (str or callable): Function to be called. Strings will be resolved to model functions.
        """
        self.get_state(state_name).add_callback("on_enter", callback)

    def on_exit(self, state_name: str, callback: str | Callback) -> None:
        """Helper function to add callbacks to states in case a custom state separator is used.
        Args:
            state_name (str): Name of the state
            callback (str or callable): Function to be called. Strings will be resolved to model functions.
        """
        self.get_state(state_name).add_callback("on_exit", callback)

    def set_state(  # type: ignore[override]
        self, state: Union[str, Enum, list[Any], "NestedState"], model: Any | None = None
    ) -> None:
        # TODO: Architectural issue - parameter type incompatible with parent class Machine
        """Set the current state.
        Args:
            state (list of str or Enum or State): value of state(s) to be set
            model (optional[object]): targeted model; if not set, all models will be set to 'state'
        """
        values = [self._set_state(value) for value in listify(state)]
        models = self.models if model is None else listify(model)
        for mod in models:
            setattr(mod, self.model_attribute, values if len(values) > 1 else values[0])

    def to_state(self, model: Any, state_name: str, *args: Any, **kwargs: Any) -> None:
        """Helper function to add go to states in case a custom state separator is used.
        Args:
            model (class): The model that should be used.
            state_name (str): Name of the destination state.
        """

        current_state = getattr(model, self.model_attribute)
        if isinstance(current_state, list):
            raise MachineError("Cannot use 'to_state' from parallel state")

        event = NestedEventData(
            self.get_state(current_state),
            Event("to", self),  # type: ignore[arg-type]
            self,
            model,
            args=args,
            kwargs=kwargs,
        )
        if isinstance(current_state, Enum):
            event.source_path = self._get_enum_path(current_state)
            event.source_name = self.state_cls.separator.join(event.source_path)  # type: ignore[arg-type]
        else:
            event.source_name = current_state
            event.source_path = current_state.split(self.state_cls.separator)
        self._create_transition(event.source_name, state_name).execute(event)

    def trigger_event(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Processes events recursively and forwards arguments if suitable events are found.
        This function is usually bound to models with model and trigger arguments already
        resolved as a partial. Execution will halt when a nested transition has been executed
        successfully.
        Args:
            model (object): targeted model
            trigger (str): event name
            *args: positional parameters passed to the event and its callbacks
            **kwargs: keyword arguments passed to the event and its callbacks
        Returns:
            bool: whether a transition has been executed successfully
        Raises:
            MachineError: When no suitable transition could be found and ignore_invalid_trigger
                          is not True. Note that a transition which is not executed due to conditions
                          is still considered valid.
        """
        event_data = NestedEventData(state=None, event=None, machine=self, model=model, args=args, kwargs=kwargs)
        event_data.result = None  # type: ignore[assignment]

        return self._process(partial(self._trigger_event, event_data, trigger))  # type: ignore[arg-type]

    def _trigger_event(self, event_data: "NestedEventData", trigger: str) -> bool | None:
        try:
            with self():
                res = self._trigger_event_nested(event_data, trigger, None)
            event_data.result = self._check_event_result(res, event_data.model, trigger)
        except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
            event_data.error = err  # type: ignore[assignment]
            if self.on_exception:
                self.callbacks(self.on_exception, event_data)
            else:
                raise
        finally:
            try:
                self.callbacks(self.finalize_event, event_data)
                _LOGGER.debug("%sExecuted machine finalize callbacks", self.name)
            except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
                _LOGGER.error("%sWhile executing finalize callbacks a %s occurred: %s.", self.name, type(err).__name__, str(err))
        return event_data.result

    def _add_model_to_state(self, state: "NestedState", model: Any) -> None:  # type: ignore[override]
        # TODO: Architectural issue - signature incompatible with parent class Machine
        name = self.get_global_name(state)
        if self.state_cls.separator == "_":
            value = state.value if isinstance(state.value, Enum) else name
            self._checked_assignment(model, "is_%s" % name, partial(self.is_state, value, model))  # type: ignore[arg-type]
            # Add dynamic method callbacks (enter/exit) if there are existing bound methods in the model
            # except if they are already mentioned in 'on_enter/exit' of the defined state
            for callback in self.state_cls.dynamic_methods:
                method = f"{callback}_{name}"
                if hasattr(model, method) and inspect.ismethod(getattr(model, method)) and method not in getattr(state, callback):
                    state.add_callback(callback, method)
        else:
            path = name.split(self.state_cls.separator)  # type: ignore[union-attr]
            value = state.value if isinstance(state.value, Enum) else name
            trig_func = partial(self.is_state, value, model)  # type: ignore[arg-type]
            # In a previous loop the base state checker is_<state> should have been added to the model.
            # If that's not the case, _checked_assignment prevented that.
            # One reason could be that model_override is True and is_<state> has not been defined on the model.
            if hasattr(model, "is_" + path[0]):
                getattr(model, "is_" + path[0]).add(trig_func, path[1:])
            elif len(path) == 1:
                self._checked_assignment(model, "is_" + path[0], FunctionWrapper(trig_func))
        with self(state.name):
            for event in self.events.values():
                self._add_trigger_to_model(event.name, model)
            for a_state in self.states.values():
                self._add_model_to_state(a_state, model)  # type: ignore[arg-type]

    def _add_dict_state(
        self, state: dict[str, Any], ignore_invalid_triggers: bool | None, remap: dict[str, str] | None, **kwargs: Any
    ) -> None:
        if remap is not None and state["name"] in remap:
            return
        state = state.copy()  # prevent messing with the initially passed dict
        remap = state.pop("remap", None)
        if "ignore_invalid_triggers" not in state:
            state["ignore_invalid_triggers"] = ignore_invalid_triggers

        # parallel: [states] is just a short handle for {children: [states], initial: [state_names]}
        state_parallel = state.pop("parallel", [])
        if state_parallel:
            state_children = state_parallel
            state["initial"] = [s["name"] if isinstance(s, dict) else s for s in state_children]
        else:
            state_children = state.pop("children", state.pop("states", []))
        transitions = state.pop("tfsm", [])
        new_state = self._create_state(**state)
        self.states[new_state.name] = new_state
        self._init_state(new_state)  # type: ignore[arg-type]
        remapped_transitions = []
        with self(new_state.name):
            self.add_states(state_children, remap=remap, **kwargs)
            if transitions:
                self.add_transitions(transitions)
            if remap is not None:
                remapped_transitions.extend(self._remap_state(new_state, remap))  # type: ignore[arg-type]

        self.add_transitions(remapped_transitions)

    def _add_enum_state(
        self,
        state: Enum,
        on_enter: str | CallbackList | None,
        on_exit: str | CallbackList | None,
        ignore_invalid_triggers: bool | None,
        remap: dict[str, str] | None,
        **kwargs: Any,
    ) -> None:
        if remap is not None and state.name in remap:
            return
        if self.state_cls.separator in state.name:
            raise ValueError(
                f"State '{state.name}' contains '{self.state_cls.separator}' which is used as state name separator. "
                "Consider changing the NestedState.separator to avoid this issue."
            )
        if state.name in self.states:
            raise ValueError(f"State {state.name} cannot be added since it already exists.")
        new_state = self._create_state(state, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs)
        self.states[new_state.name] = new_state
        self._init_state(new_state)  # type: ignore[arg-type]

    def _add_machine_states(self, state: "HierarchicalMachine", remap: dict[str, str] | None) -> None:
        new_states = [s for s in state.states.values() if remap is None or (s.name if hasattr(s, "name") else s) not in remap]
        self.add_states(new_states)
        for evt in state.events.values():
            # skip auto tfsm
            if state.auto_transitions and evt.name.startswith("to_") and evt.name.removeprefix("to_") in state.states:
                continue
            if evt.transitions and evt.name not in self.events:
                self.events[evt.name] = evt
                for model in self.models:
                    self._add_trigger_to_model(evt.name, model)
        if self.scoped.initial is None:
            self.scoped.initial = state.initial

    def _add_string_state(
        self,
        state: str,
        on_enter: str | CallbackList | None,
        on_exit: str | CallbackList | None,
        ignore_invalid_triggers: bool | None,
        remap: dict[str, str] | None,
        **kwargs: Any,
    ) -> None:
        if remap is not None and state in remap:
            return
        domains = state.split(self.state_cls.separator, 1)
        if len(domains) > 1:
            try:
                self.get_state(domains[0])
            except ValueError:
                self.add_state(domains[0], on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs)
            with self(domains[0]):
                self.add_states(domains[1], on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs)
        else:
            if state in self.states:
                raise ValueError(f"State {state} cannot be added since it already exists.")
            new_state = self._create_state(
                state, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs
            )
            self.states[new_state.name] = new_state
            self._init_state(new_state)  # type: ignore[arg-type]

    def _add_trigger_to_model(self, trigger: str, model: Any) -> None:
        trig_func = partial(self.trigger_event, model, trigger)
        self._add_may_transition_func_for_trigger(trigger, model)
        # FunctionWrappers are only necessary if a custom separator is used
        if trigger.startswith("to_") and self.state_cls.separator != "_":
            path = trigger.removeprefix("to_").split(self.state_cls.separator)
            if hasattr(model, "to_" + path[0]):
                # add path to existing function wrapper
                getattr(model, "to_" + path[0]).add(trig_func, path[1:])
            else:
                # create a new function wrapper
                self._checked_assignment(model, "to_" + path[0], FunctionWrapper(trig_func))
        else:
            self._checked_assignment(model, trigger, trig_func)

    def build_state_tree(
        self, model_states: str | list[str] | Enum, separator: str, tree: Optional["OrderedDict[str, Any]"] = None
    ) -> "OrderedDict[str, Any]":
        """Converts a list of current states into a hierarchical state tree.
        Args:
            model_states (str or list(str)):
            separator (str): The character used to separate state names
            tree (OrderedDict): The current branch to use. If not passed, create a new tree.
        Returns:
            OrderedDict: A state tree dictionary
        """
        tree = tree if tree is not None else OrderedDict()
        if isinstance(model_states, list):
            for state in model_states:
                _ = self.build_state_tree(state, separator, tree)
        else:
            tmp = tree
            if isinstance(model_states, (Enum, EnumMeta)):
                with self():
                    path = self._get_enum_path(model_states)
            else:
                path = model_states.split(separator)
            for elem in path:  # type: ignore[union-attr]
                tmp = tmp.setdefault(elem.name if hasattr(elem, "name") else elem, OrderedDict())
        return tree

    def _get_enum_path(self, enum_state: Enum, prefix: list[str] | None = None) -> list[str] | None:
        prefix = prefix or []
        if enum_state.name in self.states and self.states[enum_state.name].value == enum_state:
            return prefix + [enum_state.name]
        for name in self.states:
            with self(name):
                res = self._get_enum_path(enum_state, prefix=prefix + [name])  # type: ignore[list-item]
                if res:
                    return res
        # if we reach this point without a prefix, we looped over all nested states
        # and could not find a suitable enum state
        if not prefix:
            raise ValueError(f"Could not find path of {enum_state}.")
        return None

    def _get_state_path(self, state: "NestedState", prefix: list[str] | None = None) -> list[str]:
        prefix = prefix or []
        if state in self.states.values():
            return prefix + [state.name]
        for name in self.states:
            with self(name):
                res = self._get_state_path(state, prefix=prefix + [name])  # type: ignore[list-item]
                if res:
                    return res
        return []

    def _check_event_result(self, res: bool | None, model: Any, trigger: str) -> bool:
        if res is None:
            state_names = getattr(model, self.model_attribute)
            msg = "%sCan't trigger event '%s' from state(s) %s!" % (self.name, trigger, state_names)
            for state_name in listify(state_names):
                state = self.get_state(state_name)
                ignore = state.ignore_invalid_triggers if state.ignore_invalid_triggers is not None else self.ignore_invalid_triggers
                if not ignore:
                    # determine whether a MachineError (valid event but invalid state) ...
                    if self.has_trigger(trigger):
                        raise MachineError(msg)
                    # or AttributeError (invalid event) is appropriate
                    raise AttributeError("Do not know event named '%s'." % trigger)
            _LOGGER.warning(msg)
            res = False
        return res

    def _get_trigger(self, model: Any, trigger_name: str, *args: Any, **kwargs: Any) -> bool:
        """Convenience function added to the model to trigger events by name.
        Args:
            model (object): Model with assigned event trigger.
            trigger_name (str): Name of the trigger to be called.
            *args: Variable length argument list which is passed to the triggered event.
            **kwargs: Arbitrary keyword arguments which is passed to the triggered event.
        Returns:
            bool: True if a tfsm has been conducted or the trigger event has been queued.
        """
        return self.trigger_event(model, trigger_name, *args, **kwargs)

    def _has_state(self, state: Union["NestedState", str], raise_error: bool = False) -> bool:  # type: ignore[override]
        # TODO: Architectural issue - signature incompatible with parent class Machine (additional parameter)
        """This function
         Args:
             state (NestedState): state to be tested
             raise_error (bool): whether ValueError should be raised when the state
                                 is not registered
        Returns:
             bool: Whether state is registered in the machine
         Raises:
             ValueError: When raise_error is True and state is not registered
        """
        found = super()._has_state(state)
        if not found:
            for a_state in self.states:
                with self(a_state):
                    if self._has_state(state):
                        return True
        if not found and raise_error:
            msg = "State %s has not been added to the machine" % (state.name if hasattr(state, "name") else state)
            raise ValueError(msg)
        return found

    def _init_state(self, state: "NestedState") -> None:
        # TODO: Architectural issue - signature incompatible with parent class Machine (parameter type)
        for model in self.models:
            self._add_model_to_state(state, model)
        if self.auto_transitions:
            state_name = self.get_global_name(state.name)
            parent = state_name.split(self.state_cls.separator, 1)  # type: ignore[union-attr]
            with self():
                for a_state in self.get_nested_state_names():
                    if a_state == parent[0]:
                        self.add_transition("to_%s" % state_name, self.wildcard_all, state_name)
                    elif len(parent) == 1:
                        self.add_transition("to_%s" % a_state, state_name, a_state)
        with self(state.name):
            for substate in self.states.values():
                self._init_state(substate)  # type: ignore[arg-type]

    def _recursive_initial(self, value: str | StateName | list[Any] | None) -> str | StateName | list[Any] | None:
        if isinstance(value, str):
            path = value.split(self.state_cls.separator, 1)
            if len(path) > 1:
                state_name, suffix = path
                # make sure the passed state has been created already
                # Directly set _initial to avoid infinite recursion
                super(HierarchicalMachine, self.__class__).initial.fset(self, state_name)  # type: ignore[attr-defined]
                with self(state_name):
                    self.initial = suffix
                    self._initial = state_name + self.state_cls.separator + str(self._initial)
            else:
                # Directly set _initial to avoid infinite recursion
                super(HierarchicalMachine, self.__class__).initial.fset(self, value)  # type: ignore[attr-defined]
        elif isinstance(value, (list, tuple)):
            return [self._recursive_initial(v) for v in value]
        else:
            # Directly set _initial to avoid infinite recursion
            super(HierarchicalMachine, self.__class__).initial.fset(self, value)  # type: ignore[attr-defined]
        return self._initial[0] if isinstance(self._initial, list) and len(self._initial) == 1 else self._initial

    def _remap_state(self, state: "NestedState", remap: dict[str, str]) -> list[dict[str, Any]]:
        drop_event = []
        remapped_transitions = []
        for evt in self.events.values():
            self.events[evt.name] = copy.copy(evt)
        for trigger, event in self.events.items():
            drop_source = []
            event.transitions = copy.deepcopy(event.transitions)
            for source_name, trans_source in event.transitions.items():
                if source_name in remap:
                    drop_source.append(source_name)
                    continue
                drop_trans = []
                for trans in trans_source:
                    if trans.dest in remap:
                        conditions: list[Any] = []
                        unless: list[Any] = []
                        for cond in trans.conditions:
                            # split a list in two lists based on the accessors (cond.target) truth value
                            (unless, conditions)[cond.target].append(cond.func)
                        remapped_transitions.append({
                            "trigger": trigger,
                            "source": state.name + self.state_cls.separator + trans.source,  # type: ignore[operator]
                            "dest": remap[trans.dest],  # type: ignore[index]
                            "conditions": conditions,
                            "unless": unless,
                            "prepare": trans.prepare,
                            "before": trans.before,
                            "after": trans.after,
                        })
                        drop_trans.append(trans)
                for d_trans in drop_trans:
                    trans_source.remove(d_trans)
                if not trans_source:
                    drop_source.append(source_name)
            for d_source in drop_source:
                del event.transitions[d_source]
            if not event.transitions:
                drop_event.append(trigger)
        for d_event in drop_event:
            del self.events[d_event]
        return remapped_transitions

    def _resolve_initial(self, models: list[Any], state_name_path: list[str], prefix: list[str] | None = None) -> str | list[Any]:
        prefix = prefix or []
        if state_name_path:
            state_name = state_name_path.pop(0)
            with self(state_name):
                return self._resolve_initial(models, state_name_path, prefix=prefix + [state_name])
        if self.scoped.initial:
            entered_states = []
            for initial_state_name in listify(self.scoped.initial):
                with self(initial_state_name):
                    entered_states.append(self._resolve_initial(models, [], prefix=prefix + [self.scoped.name]))
            return entered_states if len(entered_states) > 1 else entered_states[0]
        return self.state_cls.separator.join(prefix)

    def _set_state(self, state_name: str | Enum | list[Any]) -> str | Enum | list[Any]:
        if isinstance(state_name, list):
            return [self._set_state(value) for value in state_name]
        a_state = self.get_state(state_name)
        return a_state.value if isinstance(a_state.value, Enum) else state_name

    def _trigger_event_nested(self, event_data: "NestedEventData", trigger: str, _state_tree: dict[str, Any] | None) -> bool | None:
        model = event_data.model
        if _state_tree is None:
            _state_tree = self.build_state_tree(
                listify(getattr(model, self.model_attribute)),  # type: ignore[arg-type]
                self.state_cls.separator,
            )
        res = {}
        for key, value in _state_tree.items():
            if value:
                with self(key):
                    tmp = self._trigger_event_nested(event_data, trigger, value)
                    if tmp is not None:
                        res[key] = tmp
            if res.get(key, False) is False and trigger in self.events:
                event_data.event = self.events[trigger]
                tmp = event_data.event.trigger_nested(event_data)  # type: ignore[attr-defined]
                if tmp is not None:
                    res[key] = tmp
        return None if not res or all(v is None for v in res.values()) else any(res.values())
