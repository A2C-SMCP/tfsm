"""
tfsm.core
----------------

This module contains the central parts of tfsm which are the state machine logic, state
and transition concepts.
"""

import inspect
import itertools
import logging
import warnings
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Collection
from enum import Enum, EnumMeta
from functools import partial
from typing import Any, TypeAlias, Union, cast

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())

warnings.filterwarnings(action="default", message=r".*tfsm version.*", category=DeprecationWarning)

# Type aliases for better type hints
StateName: TypeAlias = str | Enum
Callback: TypeAlias = Callable[..., Any]
CallbackList: TypeAlias = list[str | Callback]
ListifyResult: TypeAlias = list[Any] | tuple[Any, ...]
TriggerFunc: TypeAlias = "partial[Callable[..., bool]]"  # partial functions used as triggers


def listify(obj: Any) -> list[Any] | tuple[Any, ...]:
    """Wraps a passed object into a list in case it has not been a list, tuple before.
    Returns an empty list in case ``obj`` is None.
    Args:
        obj: instance to be converted into a list.
    Returns:
        list: May also return a tuple in case ``obj`` has been a tuple before.
    """
    if obj is None:
        return []

    try:
        if isinstance(obj, (list, tuple, EnumMeta)):
            return cast(list[Any] | tuple[Any, ...], obj)
        else:
            return [obj]
    except ReferenceError:
        # obj is an empty weakref
        return [obj]


def _prep_ordered_arg(desired_length: int, arguments: Any = None) -> list[Any]:
    """Ensure list of arguments passed to add_ordered_transitions has the proper length.
    Expands the given arguments and apply same condition, callback
    to all tfsm if only one has been given.

    Args:
        desired_length (int): The size of the resulting list
        arguments (optional[str, reference or list]): Parameters to be expanded.
    Returns:
        list: Parameter sets with the desired length.
    """
    if arguments is None:
        result = [None]
    else:
        result = list(listify(arguments))

    if len(result) != desired_length and len(result) != 1:
        raise ValueError("Argument length must be either 1 or the same length as the number of tfsm.")
    if len(result) == 1:
        # Expand to desired length (even if it's [None])
        result = result * desired_length

    return result


class State:
    """A persistent representation of a state managed by a ``Machine``.

    Attributes:
        _name (str): State name which is also assigned to the model(s).
        on_enter (list): Callbacks executed when a state is entered.
        on_exit (list): Callbacks executed when a state is exited.
        ignore_invalid_triggers (bool): Indicates if unhandled/invalid triggers should raise an exception.
        pocket (Any): A temporary storage space for user data. The pocket is available for use in state
            callbacks (e.g., on_enter) and will be automatically cleared when the state is exited (on_exit).
            This provides a convenient way to pass data between callbacks without using complex return value
            mechanisms. The name "pocket" is inspired by Pokemon - it can hold anything!
    """

    __slots__ = ["_name", "final", "ignore_invalid_triggers", "on_enter", "on_exit", "_pocket"]

    # A list of dynamic methods which can be resolved by a ``Machine`` instance for convenience functions.
    # Dynamic methods for states must always start with `on_`!
    dynamic_methods: list[str] = ["on_enter", "on_exit"]

    def __init__(
        self,
        name: StateName,
        on_enter: str | CallbackList | None = None,
        on_exit: str | CallbackList | None = None,
        ignore_invalid_triggers: bool | None = None,
        final: bool = False,
    ):
        """
        Args:
            name (str or Enum): The name of the state
            on_enter (str or list): Optional callable(s) to trigger when a
                state is entered. Can be either a string providing the name of
                a callable, or a list of strings.
            on_exit (str or list): Optional callable(s) to trigger when a
                state is exited. Can be either a string providing the name of a
                callable, or a list of strings.
            ignore_invalid_triggers (Boolean): Optional flag to indicate if
                unhandled/invalid triggers should raise an exception

        """
        self._name: StateName = name
        self.final: bool = final
        self.ignore_invalid_triggers: bool | None = ignore_invalid_triggers
        # Convert listify results to CallbackList (ensure we always have a list, not a tuple)
        self.on_enter: CallbackList = list(listify(on_enter)) if on_enter else []
        self.on_exit: CallbackList = list(listify(on_exit)) if on_exit else []
        self._pocket: Any = None

    @property
    def name(self) -> str:
        """The name of the state."""
        if isinstance(self._name, Enum):
            return self._name.name
        return str(self._name)

    @property
    def value(self) -> StateName:
        """The state's value. For string states this will be equivalent to the name attribute."""
        return self._name

    @property
    def pocket(self) -> Any:
        """Get the value stored in the state's pocket.

        The pocket is a temporary storage space that persists while the state is active.
        It is automatically cleared when the state is exited (during on_exit callbacks).

        Example:
            def on_enter_processing(self, event_data):
                event_data.state.pocket = {"count": 42, "status": "ok"}

            # Later, retrieve the value
            result = machine.get_state('processing').pocket

        Returns:
            The value stored in the pocket, or None if not set.
        """
        return self._pocket

    @pocket.setter
    def pocket(self, value: Any) -> None:
        """Set a value in the state's pocket.

        Args:
            value: Any value to store in the pocket. The pocket can hold any type of data.

        Example:
            event_data.state.pocket = "Hello"
            event_data.state.pocket = {"key": "value"}
            event_data.state.pocket = [1, 2, 3]
        """
        self._pocket = value

    def enter(self, event_data: "EventData") -> None:
        """Triggered when a state is entered."""
        _LOGGER.debug(f"{event_data.machine.name}Entering state {self.name}. Processing callbacks...")
        event_data.machine.callbacks(self.on_enter, event_data)
        _LOGGER.info(f"{event_data.machine.name}Finished processing state {self.name} enter callbacks.")

    def exit(self, event_data: "EventData") -> None:
        """Triggered when a state is exited."""
        _LOGGER.debug(f"{event_data.machine.name}Exiting state {self.name}. Processing callbacks...")
        event_data.machine.callbacks(self.on_exit, event_data)
        self._pocket = None
        _LOGGER.info(f"{event_data.machine.name}Finished processing state {self.name} exit callbacks.")

    def add_callback(self, trigger: str, func: str | Callback) -> None:
        """Add a new enter or exit callback.

        Args:
            trigger (str): The full method name for the callback list (e.g., 'on_enter', 'on_exit',
                or any custom method defined in dynamic_methods).
            func (str): The name of the callback function.
        """
        callback_list = getattr(self, trigger)
        callback_list.append(func)

    def __repr__(self) -> str:
        return "<%s('%s')@%s>" % (type(self).__name__, self.name, id(self))


class Condition:
    """A helper class to call condition checks in the intended way.

    Attributes:
        func (str or callable): The function to call for the condition check
        target (bool): Indicates the target state--i.e., when True,
                the condition-checking callback should return True to pass,
                and when False, the callback should return False to pass.
    """

    __slots__ = ["func", "target"]

    def __init__(self, func: str | Callback, target: bool = True):
        """
        Args:
            func (str or callable): Name of the condition-checking callable
            target (bool): Indicates the target state--i.e., when True,
                the condition-checking callback should return True to pass,
                and when False, the callback should return False to pass.
        Notes:
            This class should not be initialized or called from outside a
            Transition instance, and exists at module level (rather than
            nesting under the transition class) only because of a bug in
            dill that prevents serialization under Python 2.7.
        """
        self.func: str | Callback = func
        self.target: bool = target

    def check(self, event_data: "EventData") -> bool:
        """Check whether the condition passes.
        Args:
            event_data (EventData): An EventData instance to pass to the
                condition (if event sending is enabled) or to extract arguments
                from (if event sending is disabled). Also contains the data
                model attached to the current machine which is used to invoke
                the condition.
        """
        predicate = event_data.machine.resolve_callable(self.func, event_data)
        if event_data.machine.send_event:
            result = predicate(event_data)
            return bool(result == self.target)
        result = predicate(*event_data.args, **event_data.kwargs)
        return bool(result == self.target)

    def __repr__(self) -> str:
        return "<%s(%s)@%s>" % (type(self).__name__, self.func, id(self))


class Transition:
    """Representation of a transition managed by a ``Machine`` instance.

    Attributes:
        source (str): Source state of the transition.
        dest (str): Destination state of the transition.
        prepare (list): Callbacks executed before conditions checks.
        conditions (list): Callbacks evaluated to determine if
            the transition should be executed.
        before (list): Callbacks executed before the transition is executed
            but only if condition checks have been successful.
        after (list): Callbacks executed after the transition is executed
            but only if condition checks have been successful.
    """

    __slots__ = ["source", "dest", "prepare", "before", "after", "conditions"]

    dynamic_methods = ["before", "after", "prepare"]
    """ A list of dynamic methods which can be resolved by a ``Machine`` instance for convenience functions. """
    condition_cls = Condition
    """ The class used to wrap condition checks. Can be replaced to alter condition resolution behaviour
        (e.g. OR instead of AND for 'conditions' or AND instead of OR for 'unless') """

    def __init__(
        self,
        source: StateName,
        dest: StateName | None,
        conditions: str | Callback | CallbackList | None = None,
        unless: str | Callback | CallbackList | None = None,
        before: str | Callback | CallbackList | None = None,
        after: str | Callback | CallbackList | None = None,
        prepare: str | Callback | CallbackList | None = None,
    ):
        """
        Args:
            source (str): The name of the source State.
            dest (str): The name of the destination State.
            conditions (optional[str, callable or list]): Condition(s) that must pass in order for
                the transition to take place. Either a string providing the
                name of a callable, or a list of callables. For the transition
                to occur, ALL callables must return True.
            unless (optional[str, callable or list]): Condition(s) that must return False in order
                for the transition to occur. Behaves just like conditions arg
                otherwise.
            before (optional[str, callable or list]): callbacks to trigger before the
                transition.
            after (optional[str, callable or list]): callbacks to trigger after the transition.
            prepare (optional[str, callable or list]): callbacks to trigger before conditions are checked
        """
        self.source: StateName = source
        self.dest: StateName | None = dest

        # Convert listify results to CallbackList (ensure we always have a list, not a tuple)
        self.prepare: CallbackList = [] if prepare is None else list(listify(prepare))
        self.before: CallbackList = [] if before is None else list(listify(before))
        self.after: CallbackList = [] if after is None else list(listify(after))

        self.conditions: list[Condition] = []
        if conditions is not None:
            for cond in listify(conditions):
                self.conditions.append(self.condition_cls(cond))
        if unless is not None:
            for cond in listify(unless):
                self.conditions.append(self.condition_cls(cond, target=False))

    def _eval_conditions(self, event_data: "EventData") -> bool:
        for cond in self.conditions:
            if not cond.check(event_data):
                _LOGGER.debug(
                    f"{event_data.machine.name}Transition condition failed: {cond.func}() does not return {cond.target}. Transition halted."
                )
                return False
        return True

    def execute(self, event_data: "EventData") -> bool:
        """Execute the transition.
        Args:
            event_data: An instance of class EventData.
        Returns: boolean indicating whether the transition was
            successfully executed (True if successful, False if not).
        """
        _LOGGER.debug(f"{event_data.machine.name}Initiating transition from state {self.source} to state {self.dest}...")

        event_data.machine.callbacks(self.prepare, event_data)
        _LOGGER.debug(f"{event_data.machine.name}Executed callbacks before conditions.")

        if not self._eval_conditions(event_data):
            return False

        # Combine callbacks and convert to list for type safety
        before_callbacks = list(itertools.chain(event_data.machine.before_state_change, self.before))
        event_data.machine.callbacks(before_callbacks, event_data)
        _LOGGER.debug(f"{event_data.machine.name}Executed callback before transition.")

        if self.dest is not None:  # if self.dest is None this is an internal transition with no actual state change
            self._change_state(event_data)

        after_callbacks = list(itertools.chain(self.after, event_data.machine.after_state_change))
        event_data.machine.callbacks(after_callbacks, event_data)
        _LOGGER.debug(f"{event_data.machine.name}Executed callback after transition.")
        return True

    def _change_state(self, event_data: "EventData") -> None:
        event_data.machine.get_state(self.source).exit(event_data)
        # self.dest is guaranteed to be not None when _change_state is called
        # (checked before calling in the execute method)
        assert self.dest is not None
        event_data.machine.set_state(self.dest, event_data.model)
        event_data.update(getattr(event_data.model, event_data.machine.model_attribute))
        dest = event_data.machine.get_state(self.dest)
        dest.enter(event_data)
        if dest.final:
            event_data.machine.callbacks(event_data.machine.on_final, event_data)

    def add_callback(self, trigger: str, func: str | Callback) -> None:
        """Add a new before, after, or prepare callback.
        Args:
            trigger (str): The type of triggering event. Must be one of
                'before', 'after' or 'prepare'.
            func (str or callable): The name of the callback function or a callable.
        """
        callback_list = getattr(self, trigger)
        callback_list.append(func)

    def __repr__(self) -> str:
        return "<%s('%s', '%s')@%s>" % (type(self).__name__, self.source, self.dest, id(self))


class EventData:
    """Collection of relevant data related to the ongoing transition attempt.

    Attributes:
        state (State): The State from which the Event was triggered.
        event (Event): The triggering Event.
        machine (Machine): The current Machine instance.
        model (object): The model/object the machine is bound to.
        args (list): Optional positional arguments from trigger method
            to store internally for possible later use.
        kwargs (dict): Optional keyword arguments from trigger method
            to store internally for possible later use.
        transition (Transition): Currently active transition. Will be assigned during triggering.
        error (Exception): In case a triggered event causes an Error, it is assigned here and passed on.
        result (bool): True in case a transition has been successful, False otherwise.
    """

    __slots__ = ["state", "event", "machine", "model", "args", "kwargs", "transition", "error", "result"]

    def __init__(self, state: State | None, event: "Event", machine: "Machine", model: Any, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """
        Args:
            state (State): The State from which the Event was triggered.
            event (Event): The triggering Event.
            machine (Machine): The current Machine instance.
            model (object): The model/object the machine is bound to.
            args (tuple): Optional positional arguments from trigger method
                to store internally for possible later use.
            kwargs (dict): Optional keyword arguments from trigger method
                to store internally for possible later use.
        """
        self.state: State | None = state
        self.event: Event = event
        self.machine: Machine = machine
        self.model: Any = model
        self.args: tuple[Any, ...] = args
        self.kwargs: dict[str, Any] = kwargs
        self.transition: Transition | None = None
        self.error: Exception | None = None
        self.result: bool = False

    def update(self, state: State | StateName) -> None:
        """Updates the EventData object with the passed state.

        Attributes:
            state (State, str or Enum): The state object, enum member or string to assign to EventData.
        """

        if not isinstance(state, State):
            self.state = self.machine.get_state(state)

    def __repr__(self) -> str:
        return "<%s(%s, %s, %s)@%s>" % (type(self).__name__, self.event, self.state, self.transition, id(self))


class Event:
    """A collection of tfsm assigned to the same trigger"""

    __slots__ = ["name", "machine", "transitions"]

    def __init__(self, name: str, machine: "Machine") -> None:
        """
        Args:
            name (str): The name of the event, which is also the name of the
                triggering callable (e.g., 'advance' implies an advance()
                method).
            machine (Machine): The current Machine instance.
        """
        self.name: str = name
        self.machine: Machine = machine
        self.transitions: defaultdict[str, list[Transition]] = defaultdict(list)

    def add_transition(self, transition: "Transition") -> None:
        """Add a transition to the list of potential tfsm.
        Args:
            transition (Transition): The Transition instance to add to the
                list.
        """
        # Convert source to string key for the defaultdict
        source_key = transition.source.name if isinstance(transition.source, Enum) else transition.source
        self.transitions[source_key].append(transition)

    def trigger(self, model: Any, *args: Any, **kwargs: Any) -> bool:
        """Executes all tfsm that match the current state,
        halting as soon as one successfully completes. More precisely, it prepares a partial
        of the internal ``_trigger`` function, passes this to ``Machine._process``.
        It is up to the machine's configuration of the Event whether processing happens queued (sequentially) or
        whether further Events are processed as they occur.
        Args:
            model (object): The currently processed model
            args and kwargs: Optional positional or named arguments that will
                be passed onto the EventData object, enabling arbitrary state
                information to be passed on to downstream triggered functions.
        Returns: boolean indicating whether a transition was
            successfully executed (True if successful, False if not).
        """
        func = partial(self._trigger, EventData(None, self, self.machine, model, args=args, kwargs=kwargs))
        # pylint: disable=protected-access
        # noinspection PyProtectedMember
        # Machine._process should not be called somewhere else. That's why it should not be exposed
        # to Machine users.
        return self.machine._process(func)

    def _trigger(self, event_data: "EventData") -> bool:
        """Internal trigger function called by the ``Machine`` instance. This should not
        be called directly but via the public method ``Machine.process``.
        Args:
            event_data (EventData): The currently processed event. State, result and (potentially) error might be
            overridden.
        Returns: boolean indicating whether a transition was
            successfully executed (True if successful, False if not).
        """
        event_data.state = self.machine.get_model_state(event_data.model)
        try:
            if self._is_valid_source(event_data.state):
                self._process(event_data)
        except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
            # Cast BaseException to Exception for error storage
            # Most exceptions are Exception subclasses, but we catch BaseException to handle KeyboardInterrupt etc.
            if isinstance(err, Exception):
                event_data.error = err
            else:
                # For non-Exception BaseException types, create a wrapper
                event_data.error = Exception(f"{type(err).__name__}: {str(err)}")
            if self.machine.on_exception:
                self.machine.callbacks(self.machine.on_exception, event_data)
            else:
                raise
        finally:
            try:
                self.machine.callbacks(self.machine.finalize_event, event_data)
                _LOGGER.debug(f"{self.machine.name}Executed machine finalize callbacks")
            except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
                _LOGGER.error(f"{self.machine.name}While executing finalize callbacks a {type(err).__name__} occurred: {str(err)}")
        return event_data.result

    def _process(self, event_data: "EventData") -> None:
        self.machine.callbacks(self.machine.prepare_event, event_data)
        _LOGGER.debug(f"{self.machine.name}Executed machine preparation callbacks before conditions.")
        # event_data.state should always be set when _process is called
        # (set in _trigger before calling _process)
        assert event_data.state is not None
        for trans in self.transitions[event_data.state.name]:
            event_data.transition = trans
            if trans.execute(event_data):
                event_data.result = True
                break

    def _is_valid_source(self, state: State) -> bool:
        if state.name not in self.transitions:
            msg = "%sCan't trigger event %s from state %s!" % (self.machine.name, self.name, state.name)
            ignore = state.ignore_invalid_triggers if state.ignore_invalid_triggers is not None else self.machine.ignore_invalid_triggers
            if ignore:
                _LOGGER.warning(msg)
                return False
            raise MachineError(msg)
        return True

    def __repr__(self) -> str:
        return "<%s('%s')@%s>" % (type(self).__name__, self.name, id(self))

    def add_callback(self, trigger: str, func: str | Callback) -> None:
        """Add a new before or after callback to all available tfsm.
        Args:
            trigger (str): The type of triggering event. Must be one of
                'before', 'after' or 'prepare'.
            func (str): The name of the callback function.
        """
        for trans in itertools.chain(*self.transitions.values()):
            trans.add_callback(trigger, func)


class Machine:
    """Machine manages states, tfsm and models. In case it is initialized without a specific model
    (or specifically no model), it will also act as a model itself. Machine takes also care of decorating
    models with conveniences functions related to added tfsm and states during runtime.

    Attributes:
        states (OrderedDict): Collection of all registered states.
        events (dict): Collection of tfsm ordered by trigger/event.
        models (list): List of models attached to the machine.
        initial (str): Name of the initial state for new models.
        prepare_event (list): Callbacks executed when an event is triggered.
        before_state_change (list): Callbacks executed after condition checks but before transition is conducted.
            Callbacks will be executed BEFORE the custom callbacks assigned to the transition.
        after_state_change (list): Callbacks executed after the transition has been conducted.
            Callbacks will be executed AFTER the custom callbacks assigned to the transition.
        finalize_event (list): Callbacks will be executed after all tfsm callbacks have been executed.
            Callbacks mentioned here will also be called if a transition or condition check raised an error.
        _queued (bool): Whether tfsm in callbacks should be executed immediately (False) or sequentially.
        send_event (bool): When True, any arguments passed to trigger methods will be wrapped in an EventData
            object, allowing indirect and encapsulated access to data. When False, all positional and keyword
            arguments will be passed directly to all callback methods.
        auto_transitions (bool):  When True (default), every state will automatically have an associated
            to_{state}() convenience trigger in the base model.
        ignore_invalid_triggers (bool): When True, any calls to trigger methods that are not valid for the
            present state (e.g., calling an a_to_b() trigger when the current state is c) will be silently
            ignored rather than raising an invalid transition exception.
        name (str): Name of the ``Machine`` instance mainly used for easier log message distinction.
    """

    separator = "_"  # separates callback type from state/transition name
    wildcard_all = "*"  # will be expanded to ALL states
    wildcard_same = "="  # will be expanded to source state
    state_cls = State
    transition_cls = Transition
    event_cls = Event
    self_literal = "self"

    def __init__(
        self,
        model: Any = "self",
        states: Union[list[StateName], "OrderedDict[StateName, State]"] | None = None,
        initial: StateName = "initial",
        transitions: list[Any] | None = None,
        send_event: bool = False,
        auto_transitions: bool = True,
        ordered_transitions: bool = False,
        ignore_invalid_triggers: bool | None = None,
        before_state_change: Callback | None = None,
        after_state_change: Callback | None = None,
        name: str | None = None,
        queued: bool = False,
        prepare_event: Callback | None = None,
        finalize_event: Callback | None = None,
        model_attribute: str = "state",
        model_override: bool = False,
        on_exception: Callback | None = None,
        on_final: Callback | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            model (object or list): The object(s) whose states we want to manage. If set to `Machine.self_literal`
                (default value), the current Machine instance will be used as the model (i.e., all
                triggering events will be attached to the Machine itself). Note that an empty list
                is treated like no model.
            states (list or Enum): A list or enumeration of valid states. Each list element can be either a
                string, an enum member or a State instance. If string or enum member, a new generic State
                instance will be created that is named according to the string or enum member's name.
            initial (str, Enum or State): The initial state of the passed model[s].
            transitions (list): An optional list of tfsm. Each element
                is a dictionary of named arguments to be passed onto the
                Transition initializer.
            send_event (boolean): When True, any arguments passed to trigger
                methods will be wrapped in an EventData object, allowing
                indirect and encapsulated access to data. When False, all
                positional and keyword arguments will be passed directly to all
                callback methods.
            auto_transitions (boolean): When True (default), every state will
                automatically have an associated to_{state}() convenience
                trigger in the base model.
            ordered_transitions (boolean): Convenience argument that calls
                add_ordered_transitions() at the end of initialization if set
                to True.
            ignore_invalid_triggers: when True, any calls to trigger methods
                that are not valid for the present state (e.g., calling an
                a_to_b() trigger when the current state is c) will be silently
                ignored rather than raising an invalid transition exception.
            before_state_change: A callable called on every change state before
                the transition happened. It receives the very same args as normal
                callbacks.
            after_state_change: A callable called on every change state after
                the transition happened. It receives the very same args as normal
                callbacks.
            name: If a name is set, it will be used as a prefix for logger output
            queued (boolean): When True, processes tfsm sequentially. A trigger
                executed in a state callback function will be queued and executed later.
                Due to the nature of the queued processing, all tfsm will
                _always_ return True since conditional checks cannot be conducted at queueing time.
            prepare_event: A callable called on for before possible tfsm will be processed.
                It receives the very same args as normal callbacks.
            finalize_event: A callable called on for each triggered event after tfsm have been processed.
                This is also called when a transition raises an exception.
            on_exception: A callable called when an event raises an exception. If not set,
                the exception will be raised instead.

            **kwargs additional arguments passed to next class in MRO. This can be ignored in most cases.
        """

        # Handle legacy markup format where "tfsm" was used as a key instead of "transitions"
        # This is needed for backward compatibility with old markup configurations
        if "tfsm" in kwargs and transitions is None:
            transitions = kwargs.pop("tfsm")

        # calling super in case `Machine` is used as a mix in
        # all keyword arguments should be consumed by now if this is not the case
        try:
            super().__init__(**kwargs)
        except TypeError as err:
            raise ValueError(f"Passing arguments {kwargs.keys()} caused an inheritance error: {err}")

        # initialize protected attributes first
        self._queued = queued
        # Use Any for transition queue since partial functions have dynamic attributes
        self._transition_queue: deque[Any] = deque()
        self._before_state_change: CallbackList = []
        self._after_state_change: CallbackList = []
        self._prepare_event: CallbackList = []
        self._finalize_event: CallbackList = []
        self._on_exception: CallbackList = []
        self._on_final: CallbackList = []
        self._initial: StateName | None = None

        self.states: OrderedDict[StateName, State] = OrderedDict()
        self.events: OrderedDict[str, Event] = OrderedDict()
        self.send_event = send_event
        self.auto_transitions = auto_transitions
        self.ignore_invalid_triggers = ignore_invalid_triggers
        self.prepare_event = prepare_event
        self.before_state_change = before_state_change
        self.after_state_change = after_state_change
        self.finalize_event = finalize_event
        self.on_exception = on_exception
        self.on_final = on_final
        self.name = name + ": " if name is not None else ""
        self.model_attribute = model_attribute
        self.model_override = model_override

        self.models: list[Any] = []

        if states is not None:
            self.add_states(states)

        if initial is not None:
            self.initial = initial

        if transitions is not None:
            self.add_transitions(transitions)

        if ordered_transitions:
            self.add_ordered_transitions()

        if model:
            self.add_model(model)

    def add_model(self, model: Any | list[Any], initial: StateName | None = None) -> "Machine":
        """Register a model with the state machine, initializing triggers and callbacks."""
        models = listify(model)

        if initial is None:
            if self.initial is None:
                raise ValueError("No initial state configured for machine, must specify when adding model.")
            initial = self.initial

        for mod in models:
            mod = self if mod is self.self_literal else mod
            if mod not in self.models:
                self._checked_assignment(mod, "trigger", partial(self._get_trigger, mod))
                self._checked_assignment(mod, "may_trigger", partial(self._can_trigger, mod))

                for trigger in self.events:
                    self._add_trigger_to_model(trigger, mod)

                for state in self.states.values():
                    self._add_model_to_state(state, mod)

                self.set_state(initial, model=mod)
                self.models.append(mod)

        return self

    def remove_model(self, model: Any | list[Any]) -> None:
        """Remove a model from the state machine. The model will still contain all previously added triggers
        and callbacks, but will not receive updates when states or tfsm are added to the Machine.
        If an event queue is used, all queued events of that model will be removed."""
        models = listify(model)

        for mod in models:
            self.models.remove(mod)
        if len(self._transition_queue) > 0:
            # the first element of the list is currently executed. Keeping it for further Machine._process(ing)
            self._transition_queue = deque(
                [self._transition_queue[0]] + [e for e in self._transition_queue if e.args[0].model not in models]
            )

    @classmethod
    def _create_transition(cls, *args: Any, **kwargs: Any) -> Transition:
        return cls.transition_cls(*args, **kwargs)

    @classmethod
    def _create_event(cls, *args: Any, **kwargs: Any) -> Event:
        return cls.event_cls(*args, **kwargs)

    @classmethod
    def _create_state(cls, *args: Any, **kwargs: Any) -> State:
        return cls.state_cls(*args, **kwargs)

    @property
    def initial(self) -> StateName | None:
        """Return the initial state."""
        return self._initial

    @initial.setter
    def initial(self, value: StateName | State) -> None:
        if isinstance(value, State):
            if value.name not in self.states:
                self.add_states(value)
            else:
                _ = self._has_state(value, raise_error=True)
            self._initial = value.name
        else:
            state_name = value.name if isinstance(value, Enum) else value
            if state_name not in self.states:
                self.add_states(state_name)
            self._initial = state_name

    @property
    def has_queue(self) -> bool:
        """Return boolean indicating if machine has queue or not"""
        return self._queued

    @property
    def model(self) -> Any | list[Any]:
        """List of models attached to the machine. For backwards compatibility, the property will
        return the model instance itself instead of the underlying list  if there is only one attached
        to the machine.
        """
        if len(self.models) == 1:
            return self.models[0]
        return self.models

    @property
    def before_state_change(self) -> CallbackList:
        """Callbacks executed after condition checks but before transition is conducted.
        Callbacks will be executed BEFORE the custom callbacks assigned to the transition."""
        return self._before_state_change

    # this should make sure that _before_state_change is always a list
    @before_state_change.setter
    def before_state_change(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._before_state_change = list(listify(value)) if value is not None else []

    @property
    def after_state_change(self) -> CallbackList:
        """Callbacks executed after the transition has been conducted.
        Callbacks will be executed AFTER the custom callbacks assigned to the transition."""
        return self._after_state_change

    # this should make sure that _after_state_change is always a list
    @after_state_change.setter
    def after_state_change(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._after_state_change = list(listify(value)) if value is not None else []

    @property
    def prepare_event(self) -> CallbackList:
        """Callbacks executed when an event is triggered."""
        return self._prepare_event

    # this should make sure that prepare_event is always a list
    @prepare_event.setter
    def prepare_event(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._prepare_event = list(listify(value)) if value is not None else []

    @property
    def finalize_event(self) -> CallbackList:
        """Callbacks will be executed after all tfsm callbacks have been executed.
        Callbacks mentioned here will also be called if a transition or condition check raised an error."""
        return self._finalize_event

    # this should make sure that finalize_event is always a list
    @finalize_event.setter
    def finalize_event(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._finalize_event = list(listify(value)) if value is not None else []

    @property
    def on_exception(self) -> CallbackList:
        """Callbacks will be executed when an Event raises an Exception."""
        return self._on_exception

    # this should make sure that finalize_event is always a list
    @on_exception.setter
    def on_exception(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._on_exception = list(listify(value)) if value is not None else []

    @property
    def on_final(self) -> CallbackList:
        """Callbacks will be executed when the reached state is tagged with 'final'"""
        return self._on_final

    # this should make sure that finalize_event is always a list
    @on_final.setter
    def on_final(self, value: str | Callback | CallbackList | None) -> None:
        # Convert listify result to CallbackList (ensure we always have a list, not a tuple)
        self._on_final = list(listify(value)) if value is not None else []

    def get_state(self, state: StateName) -> State:
        """Return the State instance with the passed name."""
        if isinstance(state, Enum):
            state = state.name
        if state not in self.states:
            raise ValueError("State '%s' is not a registered state." % state)
        return self.states[state]

    # In theory this function could be static. This however causes some issues related to inheritance and
    # pickling down the chain.
    def is_state(self, state: StateName, model: Any) -> bool:
        """Check whether the current state matches the named state. This function is not called directly
            but assigned as partials to model instances (e.g. is_A -> partial(_is_state, 'A', model)).
        Args:
            state (str or Enum): name of the checked state or Enum
            model: model to be checked
        Returns:
            bool: Whether the model's current state is state.
        """
        return bool(getattr(model, self.model_attribute) == state)

    def get_model_state(self, model: Any) -> State:
        """
            Get the state of a model
        Args:
            model (object): the stateful model
        Returns:
            State: The State object related to the model's state
        """
        return self.get_state(getattr(model, self.model_attribute))

    def set_state(self, state: StateName | State, model: Any | None = None) -> None:
        """
            Set the current state.
        Args:
            state (str or Enum or State): value of state to be set
            model (optional[object]): targeted model; if not set, all models will be set to 'state'
        """
        if not isinstance(state, State):
            state = self.get_state(state)
        models = self.models if model is None else listify(model)

        for mod in models:
            setattr(mod, self.model_attribute, state.value)

    def add_state(
        self,
        states: Union[list[StateName], StateName, "OrderedDict[StateName, State]", dict[str, Any], State],
        on_enter: str | CallbackList | None = None,
        on_exit: str | CallbackList | None = None,
        ignore_invalid_triggers: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Alias for add_states."""
        self.add_states(states=states, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore_invalid_triggers, **kwargs)

    def add_states(
        self,
        states: Union[list[StateName], StateName, "OrderedDict[StateName, State]", dict[str, Any], State],
        on_enter: str | CallbackList | None = None,
        on_exit: str | CallbackList | None = None,
        ignore_invalid_triggers: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Add new state(s).
        Args:
            states (list, str, dict, Enum or State): a list, a State instance, the
                name of a new state, an enumeration (member) or a dict with keywords to pass on to the
                State initializer. If a list, each element can be a string, State or enumeration member.
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

        ignore = ignore_invalid_triggers
        if ignore is None:
            ignore = self.ignore_invalid_triggers

        # Convert to list to handle both list and tuple returns from listify
        states_list = list(listify(states))

        for state in states_list:
            if isinstance(state, (str, Enum)):
                state = self._create_state(state, on_enter=on_enter, on_exit=on_exit, ignore_invalid_triggers=ignore, **kwargs)
            elif isinstance(state, dict):
                if "ignore_invalid_triggers" not in state:
                    state["ignore_invalid_triggers"] = ignore
                state = self._create_state(**state)
            self.states[state.name] = state
            for model in self.models:
                self._add_model_to_state(state, model)
            if self.auto_transitions:
                for a_state in self.states.keys():
                    # add all states as sources to auto tfsm 'to_<state>' with dest <state>
                    if a_state == state.name:
                        if self.model_attribute == "state":
                            method_name = "to_%s" % a_state
                        else:
                            method_name = "to_%s_%s" % (self.model_attribute, a_state)
                        self.add_transition(method_name, self.wildcard_all, a_state)

                    # add auto transition with source <state> to <a_state>
                    else:
                        if self.model_attribute == "state":
                            method_name = "to_%s" % a_state
                        else:
                            method_name = "to_%s_%s" % (self.model_attribute, a_state)
                        self.add_transition(method_name, state.name, a_state)

    def _add_model_to_state(self, state: State, model: Any) -> None:
        # Add convenience function 'is_<state_name>' (e.g. 'is_A') to the model.
        # When model_attribute has been customized, add 'is_<model_attribute>_<state_name>' instead
        # to potentially support multiple states on one model (e.g. 'is_custom_state_A' and 'is_my_state_B').

        func = partial(self.is_state, state.value, model)
        if self.model_attribute == "state":
            method_name = "is_%s" % state.name
        else:
            method_name = "is_%s_%s" % (self.model_attribute, state.name)
        self._checked_assignment(model, method_name, func)

        # Add dynamic method callbacks (enter/exit) if there are existing bound methods in the model
        # except if they are already mentioned in 'on_enter/exit' of the defined state
        for callback in self.state_cls.dynamic_methods:
            method = f"{callback}_{state.name}"
            if hasattr(model, method) and inspect.ismethod(getattr(model, method)) and method not in getattr(state, callback):
                state.add_callback(callback, method)

    def _checked_assignment(self, model: Any, name: str, func: Callable[..., Any]) -> None:
        bound_func = getattr(model, name, None)
        if (bound_func is None) ^ self.model_override:
            setattr(model, name, func)
        else:
            _LOGGER.warning(f"{self.name}Skip binding of '{name}' to model due to model override policy.")

    def _can_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        state = self.get_model_state(model)
        event_data = EventData(state, Event(name=trigger, machine=self), self, model, args, kwargs)

        for trigger_name in self.get_triggers(state):
            if trigger_name != trigger:
                continue
            for transition in self.events[trigger_name].transitions[state.name]:
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
                except BaseException as err:
                    # Cast BaseException to Exception for error storage
                    if isinstance(err, Exception):
                        event_data.error = err
                    else:
                        # For non-Exception BaseException types, create a wrapper
                        event_data.error = Exception(f"{type(err).__name__}: {str(err)}")
                    if self.on_exception:
                        self.callbacks(self.on_exception, event_data)
                    else:
                        raise
        return False

    def _add_may_transition_func_for_trigger(self, trigger: str, model: Any) -> None:
        self._checked_assignment(model, "may_%s" % trigger, partial(self._can_trigger, model, trigger))

    def _add_trigger_to_model(self, trigger: str, model: Any) -> None:
        self._checked_assignment(model, trigger, partial(self.events[trigger].trigger, model))
        self._add_may_transition_func_for_trigger(trigger, model)

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
        try:
            event = self.events[trigger_name]
        except KeyError:
            state = self.get_model_state(model)
            ignore = state.ignore_invalid_triggers if state.ignore_invalid_triggers is not None else self.ignore_invalid_triggers
            if not ignore:
                raise AttributeError("Do not know event named '%s'." % trigger_name)
            return False
        return event.trigger(model, *args, **kwargs)

    def get_triggers(self, *args: Any) -> list[str]:
        """Collects all triggers FROM certain states.
        Args:
            *args: Tuple of source states.

        Returns:
            list of transition/trigger names.
        """
        names = {state.name if hasattr(state, "name") else state for state in args}
        return [t for (t, ev) in self.events.items() if any(name in ev.transitions for name in names)]

    def add_transition(
        self,
        trigger: str,
        source: StateName | list[StateName],
        dest: StateName | None,
        conditions: str | Callback | CallbackList | None = None,
        unless: str | Callback | CallbackList | None = None,
        before: str | Callback | CallbackList | None = None,
        after: str | Callback | CallbackList | None = None,
        prepare: str | Callback | CallbackList | None = None,
        **kwargs: Any,
    ) -> None:
        """Create a new Transition instance and add it to the internal list.
        Args:
            trigger (str): The name of the method that will trigger the
                transition. This will be attached to the currently specified
                model (e.g., passing trigger='advance' will create a new
                advance() method in the model that triggers the transition.)
            source(str, Enum or list): The name of the source state--i.e., the state we
                are transitioning away from. This can be a single state, a
                list of states or an asterisk for all states.
            dest (str or Enum): The name of the destination State--i.e., the state
                we are transitioning into. This can be a single state or an
                equal sign to specify that the transition should be reflexive
                so that the destination will be the same as the source for
                every given source. If dest is None, this transition will be
                an internal transition (exit/enter callbacks won't be processed).
            conditions (str or list): Condition(s) that must pass in order
                for the transition to take place. Either a list providing the
                name of a callable, or a list of callables. For the transition
                to occur, ALL callables must return True.
            unless (str or list): Condition(s) that must return False in order
                for the transition to occur. Behaves just like conditions arg
                otherwise.
            before (str or list): Callables to call before the transition.
            after (str or list): Callables to call after the transition.
            prepare (str or list): Callables to call when the trigger is activated
            **kwargs: Additional arguments which can be passed to the created transition.
                This is useful if you plan to extend Machine.Transition and require more parameters.
        """
        if trigger == self.model_attribute:
            raise ValueError("Trigger name cannot be same as model attribute name.")
        if trigger not in self.events:
            self.events[trigger] = self._create_event(trigger, self)
            for model in self.models:
                self._add_trigger_to_model(trigger, model)

        if source == self.wildcard_all:
            source = list(self.states.keys())
        else:
            # states are checked lazily which means we will only raise exceptions when the passed state
            # is a State object because of potential confusion (see issue #155 for more details)
            source = [
                s.name if isinstance(s, State) and self._has_state(s, raise_error=True) or hasattr(s, "name") else s
                for s in listify(source)
            ]

        for state in source:
            if dest == self.wildcard_same:
                _dest = state
            elif dest is not None:
                if isinstance(dest, State):
                    _ = self._has_state(dest, raise_error=True)
                _dest = dest.name if hasattr(dest, "name") else dest
            else:
                _dest = None
            _trans = self._create_transition(state, _dest, conditions, unless, before, after, prepare, **kwargs)
            self.events[trigger].add_transition(_trans)

    def add_transitions(self, transitions: list[Any] | Any) -> None:
        """Add several tfsm.

        Args:
            transitions (list): A list of tfsm.

        """
        for trans in listify(transitions):
            if isinstance(trans, list):
                self.add_transition(*trans)
            else:
                self.add_transition(**trans)

    def add_ordered_transitions(
        self,
        states: list[StateName] | None = None,
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
        """Add a set of tfsm that move linearly from state to state.
        Args:
            states (list): A list of state names defining the order of the
                tfsm. E.g., ['A', 'B', 'C'] will generate tfsm
                for A --> B, B --> C, and C --> A (if loop is True). If states
                is None, all states in the current instance will be used.
            trigger (str): The name of the trigger method that advances to
                the next state in the sequence.
            loop (boolean): Whether to add a transition from the last
                state to the first state.
            loop_includes_initial (boolean): If no initial state was defined in
                the machine, setting this to True will cause the _initial state
                placeholder to be included in the added tfsm. This argument
                has no effect if the states argument is passed without the
                initial state included.
            conditions (str or list): Condition(s) that must pass in order
                for the transition to take place. Either a list providing the
                name of a callable, or a list of callables. For the transition
                to occur, ALL callables must return True.
            unless (str or list): Condition(s) that must return False in order
                for the transition to occur. Behaves just like conditions arg
                otherwise.
            before (str or list): Callables to call before the transition.
            after (str or list): Callables to call after the transition.
            prepare (str or list): Callables to call when the trigger is activated
            **kwargs: Additional arguments which can be passed to the created transition.
                This is useful if you plan to extend Machine.Transition and require more parameters.
        """
        if states is None:
            states = list(self.states.keys())  # need to listify for Python3
        len_transitions = len(states)
        if len_transitions < 2:
            raise ValueError("Can't create ordered tfsm on a Machine with fewer than 2 states.")
        if not loop:
            len_transitions -= 1
        # ensure all args are the proper length
        conditions = _prep_ordered_arg(len_transitions, conditions)
        unless = _prep_ordered_arg(len_transitions, unless)
        before = _prep_ordered_arg(len_transitions, before)
        after = _prep_ordered_arg(len_transitions, after)
        prepare = _prep_ordered_arg(len_transitions, prepare)
        # reorder list so that the initial state is actually the first one
        try:
            # self._initial is guaranteed to be set when we reach this point
            # (checked in add_states which is called during __init__)
            assert self._initial is not None
            idx = states.index(self._initial)
            states = states[idx:] + states[:idx]
            first_in_loop = states[0 if loop_includes_initial else 1]
        except ValueError:
            # since initial is not part of states it shouldn't be part of the loop either
            first_in_loop = states[0]

        for i in range(0, len(states) - 1):
            self.add_transition(
                trigger,
                states[i],
                states[i + 1],
                conditions=conditions[i],
                unless=unless[i],
                before=before[i],
                after=after[i],
                prepare=prepare[i],
                **kwargs,
            )
        if loop:
            self.add_transition(
                trigger,
                states[-1],
                # omit initial if not loop_includes_initial
                first_in_loop,
                conditions=conditions[-1],
                unless=unless[-1],
                before=before[-1],
                after=after[-1],
                prepare=prepare[-1],
                **kwargs,
            )

    def get_transitions(self, trigger: str = "", source: str | StateName = "*", dest: str | StateName = "*") -> list["Transition"]:
        """Return the tfsm from the Machine.
        Args:
            trigger (str): Trigger name of the transition.
            source (str, Enum or State): Limits list to tfsm from a certain state.
            dest (str, Enum or State): Limits list to tfsm to a certain state.
        """
        if trigger:
            try:
                events: tuple[Event, ...] | Collection[Event] = (self.events[trigger],)
            except KeyError:
                return []
        else:
            events = self.events.values()
        transitions: list[Transition] = []
        for event in events:
            transitions.extend(itertools.chain.from_iterable(event.transitions.values()))
        target_source = source.name if hasattr(source, "name") else source if source != "*" else ""
        target_dest = dest.name if hasattr(dest, "name") else dest if dest != "*" else ""
        return [
            transition
            for transition in transitions
            if (transition.source, transition.dest) == (target_source or transition.source, target_dest or transition.dest)
        ]

    def remove_transition(self, trigger: str, source: str | StateName = "*", dest: str | StateName = "*") -> None:
        """Removes a transition from the Machine and all models.
        Args:
            trigger (str): Trigger name of the transition.
            source (str, Enum or State): Limits removal to tfsm from a certain state.
            dest (str, Enum or State): Limits removal to tfsm to a certain state.
        """
        # Convert source/dest to lists if needed for filtering
        source_list: list[Any] | str = [s.name if hasattr(s, "name") else s for s in listify(source)] if source != "*" else "*"
        dest_list: list[Any] | str = [d.name if hasattr(d, "name") else d for d in listify(dest)] if dest != "*" else "*"
        # outer comprehension, keeps events if inner comprehension returns lists with length > 0
        tmp = {
            key: value
            for key, value in {
                k: [
                    t
                    for t in v
                    # keep entries if source should not be filtered; same for dest.
                    if (source_list != "*" and t.source not in source_list) or (dest_list != "*" and t.dest not in dest_list)
                ]
                # }.items() takes the result of the inner comprehension and uses it
                # for the outer comprehension (see first line of comment)
                for k, v in self.events[trigger].transitions.items()
            }.items()
            if len(value) > 0
        }
        # convert dict back to defaultdict in case tmp is not empty
        if tmp:
            self.events[trigger].transitions = defaultdict(list, **tmp)
        # if no transition is left remove the trigger from the machine and all models
        else:
            for model in self.models:
                delattr(model, trigger)
            del self.events[trigger]

    def dispatch(self, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Trigger an event on all models assigned to the machine.
        Args:
            trigger (str): Event name
            *args (list): List of arguments passed to the event trigger
            **kwargs (dict): Dictionary of keyword arguments passed to the event trigger
        Returns:
            bool The truth value of all triggers combined with AND
        """
        res = [getattr(model, trigger)(*args, **kwargs) for model in self.models]
        return all(res)

    def callbacks(self, funcs: CallbackList, event_data: "EventData") -> None:
        """Triggers a list of callbacks"""
        for func in funcs:
            self.callback(func, event_data)
            _LOGGER.info(f"{self.name}Executed callback '{func}'")

    def callback(self, func: str | Callback, event_data: "EventData") -> None:
        """Trigger a callback function with passed event_data parameters. In case func is a string,
            the callable will be resolved from the passed model in event_data. This function is not intended to
            be called directly but through state and transition callback definitions.
        Args:
            func (str or callable): The callback function.
                1. First, if the func is callable, just call it
                2. Second, we try to import string assuming it is a path to a func
                3. Fallback to a model attribute
            event_data (EventData): An EventData instance to pass to the
                callback (if event sending is enabled) or to extract arguments
                from (if event sending is disabled).
        """

        func = self.resolve_callable(func, event_data)
        if self.send_event:
            func(event_data)
        else:
            func(*event_data.args, **event_data.kwargs)

    @staticmethod
    def resolve_callable(func: str | Callback, event_data: "EventData") -> Callback:
        """Converts a model's property name, method name or a path to a callable into a callable.
            If func is not a string it will be returned unaltered.
        Args:
            func (str or callable): Property name, method name or a path to a callable
            event_data (EventData): Currently processed event
        Returns:
            callable function resolved from string or func
        """
        if isinstance(func, str):
            try:
                resolved_func = getattr(event_data.model, func)
                if not callable(resolved_func):  # if a property or some other not callable attribute was passed

                    def func_wrapper(*_: Any, **__: Any) -> Any:  # properties cannot process parameters
                        return resolved_func

                    return func_wrapper
                return cast(Callback, resolved_func)
            except AttributeError:
                try:
                    module_name, func_name = func.rsplit(".", 1)
                    module = __import__(module_name)
                    for submodule_name in module_name.split(".")[1:]:
                        module = getattr(module, submodule_name)
                    return cast(Callback, getattr(module, func_name))
                except (ImportError, AttributeError, ValueError):
                    raise AttributeError(
                        "Callable with name '%s' could neither be retrieved from the passed model nor imported from a module." % func
                    )
        return func

    def _has_state(self, state: State | StateName, raise_error: bool = False) -> bool:
        found = state in self.states.values()
        if not found and raise_error:
            msg = "State %s has not been added to the machine" % (state.name if hasattr(state, "name") else state)
            raise ValueError(msg)
        return found

    # TODO: Refactor _process to support per-model queues for better multi-model isolation
    #
    # Current implementation uses a single global queue (self._transition_queue) shared by all models.
    # This can be improved by adopting the per-model queue design from AsyncMachine:
    #
    # Implementation plan:
    # 1. Change `queued` parameter type from `bool` to `bool | str` to support:
    #    - queued=False (default): No queue, execute immediately
    #    - queued=True: Single global queue (current behavior, backward compatible)
    #    - queued="model": Per-model queue isolation (new feature)
    #
    # 2. Replace self._transition_queue with self._transition_queue_dict:
    #    - Type: dict[int, deque[Callable[[], bool]]]
    #    - Key: id(model) for per-model isolation
    #    - For queued=True: Use a dict-like wrapper around single queue (backward compatible)
    #    - For queued="model": Create separate deque for each model
    #
    # 3. Update _process signature to include model parameter:
    #    - def _process(self, trigger: Callable[[], bool], model: Any) -> bool:
    #    - Access queue via: self._transition_queue_dict[id(model)]
    #
    # 4. Update _transition_queue initialization in __init__:
    #    - Initialize based on queued value (True vs "model")
    #    - Update add_model/remove_model to manage per-model queues
    #
    # 5. Update all callers to pass model parameter:
    #    - Event._trigger() -> machine._process(trigger, event_data.model)
    #
    # Benefits:
    # - Better isolation between models in multi-model scenarios
    # - Prevents one model's long queue from blocking other models
    # - Consistent design with AsyncMachine (see tfsm/extensions/asyncio.py)
    # - Maintains backward compatibility via queued=True mode
    #
    # Reference: AsyncMachine implementation in tfsm/extensions/asyncio.py:1169-1193
    def _process(self, trigger: Callable[[], bool]) -> bool:

        # default processing
        if not self.has_queue:
            if not self._transition_queue:
                # if trigger raises an Error, it has to be handled by the Machine.process caller
                return trigger()
            raise MachineError("Attempt to process events synchronously while transition queue is not empty!")

        # process queued events
        self._transition_queue.append(trigger)
        # another entry in the queue implies a running transition; skip immediate execution
        if len(self._transition_queue) > 1:
            return True

        # execute as long as transition queue is not empty
        while self._transition_queue:
            try:
                self._transition_queue[0]()
                self._transition_queue.popleft()
            except BaseException:
                # if a transition raises an exception, clear queue and delegate exception handling
                self._transition_queue.clear()
                raise
        return True

    def _identify_callback(self, name: str) -> tuple[str | None, str | None]:
        # Does the prefix match a known callback?
        for callback in itertools.chain(self.state_cls.dynamic_methods, self.transition_cls.dynamic_methods):
            if name.startswith(callback):
                callback_type = callback
                break
        else:
            return None, None

        # Extract the target by cutting the string after the type and separator
        target = name[len(callback_type) + len(self.separator) :]

        # Make sure there is actually a target to avoid index error and enforce _ as a separator
        if target == "" or name[len(callback_type)] != self.separator:
            return None, None

        return callback_type, target

    def __getattr__(self, name: str) -> Any:
        # Machine.__dict__ does not contain double underscore variables.
        # Class variables will be mangled.
        if name.startswith("__"):
            raise AttributeError(f"'{name}' does not exist on <Machine@{id(self)}>")

        # Could be a callback
        callback_type, target = self._identify_callback(name)

        if callback_type is not None:
            if callback_type in self.transition_cls.dynamic_methods:
                # target is guaranteed to be not None here
                assert target is not None
                if target not in self.events:
                    raise AttributeError(f"event '{target}' is not registered on <Machine@{id(self)}>")
                return partial(self.events[target].add_callback, callback_type)

            if callback_type in self.state_cls.dynamic_methods:
                # target is guaranteed to be not None here
                assert target is not None
                state = self.get_state(target)
                return partial(state.add_callback, callback_type)

        try:
            return self.__getattribute__(name)
        except AttributeError:
            # Nothing matched
            raise AttributeError(f"'{name}' does not exist on <Machine@{id(self)}>")


class MachineError(Exception):
    """MachineError is used for issues related to state tfsm and current states.
    For instance, it is raised for invalid tfsm or machine configuration issues.
    """

    def __init__(self, value: Any) -> None:
        super().__init__(value)
        self.value: Any = value

    def __str__(self) -> str:
        return repr(self.value)
