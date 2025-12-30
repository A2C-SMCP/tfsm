"""
tfsm.extensions.factory
------------------------------

Adds locking to machine methods as well as model functions that trigger events.
Additionally, the user can inject her/his own context manager into the machine if required.
"""

import inspect
import logging
from collections import defaultdict
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from functools import partial
from threading import Lock, get_ident
from typing import Any

from tfism.core import Event, Machine, listify

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())


@contextmanager
def nested(*contexts: Any) -> Generator[tuple[Any, ...], None, None]:
    """Reimplementation of contextlib.nested for Python 3.

    This function was deprecated in Python 3.3 and removed in later versions.
    The contextlib.nested function from Python 2 has been reimplemented using ExitStack.
    """
    with ExitStack() as stack:
        for ctx in contexts:
            stack.enter_context(ctx)
        yield contexts


class PicklableLock:
    """A wrapper for threading.Lock which discards its state during pickling and
    is reinitialized unlocked when unpickled.
    """

    def __init__(self) -> None:
        self.lock: Lock = Lock()

    def __getstate__(self) -> str:
        return ""

    def __setstate__(self, value: str) -> None:
        PicklableLock.__init__(self)

    def __enter__(self) -> None:
        self.lock.__enter__()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.lock.__exit__(exc_type, exc_val, exc_tb)


class IdentManager:
    """Manages the identity of threads to detect whether the current thread already has a lock."""

    def __init__(self) -> None:
        self.current: int = 0

    def __enter__(self) -> None:
        self.current = get_ident()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.current = 0


class LockedEvent(Event):
    """An event type which uses the parent's machine context map when triggered."""

    def trigger(self, model: Any, *args: Any, **kwargs: Any) -> bool:
        """Extends tfsm.core.Event.trigger by using locks/machine contexts."""
        # pylint: disable=protected-access
        # noinspection PyProtectedMember
        # LockedMachine._locked should not be called somewhere else. That's why it should not be exposed
        # to Machine users.
        if self.machine._ident.current != get_ident():
            with nested(*self.machine.model_context_map[id(model)]):
                return super().trigger(model, *args, **kwargs)
        else:
            return super().trigger(model, *args, **kwargs)


class LockedMachine(Machine):
    """Machine class which manages contexts. In it's default version the machine uses a `threading.Lock`
        context to lock access to its methods and event triggers bound to model objects.
    Attributes:
        machine_context (dict): A dict of context managers to be entered whenever a machine method is
            called or an event is triggered. Contexts are managed for each model individually.
    """

    event_cls = LockedEvent

    def __init__(
        self,
        model: Any = Machine.self_literal,
        states: Any = None,
        initial: Any = "initial",
        transitions: Any = None,
        send_event: bool = False,
        auto_transitions: bool = True,
        ordered_transitions: bool = False,
        ignore_invalid_triggers: Any = None,
        before_state_change: Any = None,
        after_state_change: Any = None,
        name: Any = None,
        queued: bool = False,
        prepare_event: Any = None,
        finalize_event: Any = None,
        model_attribute: str = "state",
        model_override: bool = False,
        on_exception: Any = None,
        on_final: Any = None,
        machine_context: Any = None,
        **kwargs: Any,
    ) -> None:

        self._ident: IdentManager = IdentManager()
        machine_context_list: list[Any] | tuple[Any, ...] = listify(machine_context) or [PicklableLock()]
        self.machine_context: list[Any] = list(machine_context_list)
        self.machine_context.append(self._ident)
        self.model_context_map: defaultdict[Any, list[Any]] = defaultdict(list)

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

    # When we attempt to pickle a locked machine, using IDs wont suffice to unpickle the contexts since
    # IDs have changed. We use a 'reference' store with objects as dictionary keys to resolve the newly created
    # references. This should induce no restrictions compared to tfsm 0.8.8 but enable the usage of unhashable
    # objects in locked machine.
    def __getstate__(self) -> dict[str, Any]:
        state = {k: v for k, v in self.__dict__.items()}
        del state["model_context_map"]
        state["_model_context_map_store"] = {mod: self.model_context_map[id(mod)] for mod in self.models}
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.model_context_map = defaultdict(list)
        for model in self.models:
            self.model_context_map[id(model)] = self._model_context_map_store[model]
        del self._model_context_map_store

    def add_model(self, model: Any, initial: Any = None, model_context: Any = None) -> Any:
        """Extends `tfsm.core.Machine.add_model` by `model_context` keyword.
        Args:
            model (list or object): A model (list) to be managed by the machine.
            initial (str, Enum or State): The initial state of the passed model[s].
            model_context (list or object): If passed, assign the context (list) to the machines
                model specific context map.
        """
        models = listify(model)
        model_context_list = listify(model_context) if model_context is not None else []
        super().add_model(models, initial)

        for mod in models:
            mod = self if mod is self.self_literal else mod
            self.model_context_map[id(mod)].extend(self.machine_context)
            self.model_context_map[id(mod)].extend(model_context_list)

    def remove_model(self, model: Any) -> Any:
        """Extends `tfsm.core.Machine.remove_model` by removing model specific context maps
        from the machine when the model itself is removed."""
        models = listify(model)

        for mod in models:
            del self.model_context_map[id(mod)]

        return super().remove_model(models)

    def __getattribute__(self, item: str) -> Any:
        get_attr = super().__getattribute__
        tmp = get_attr(item)
        if not item.startswith("_") and inspect.ismethod(tmp):
            return partial(get_attr("_locked_method"), tmp)
        return tmp

    def __getattr__(self, item: str) -> Any:
        try:
            return super().__getattribute__(item)
        except AttributeError:
            return super().__getattr__(item)

    # Determine if the returned method is a partial and make sure the returned partial has
    # not been created by Machine.__getattr__.
    # https://github.com/tyarkoni/transitions/issues/214
    def _add_model_to_state(self, state: Any, model: Any) -> None:
        super()._add_model_to_state(state, model)  # pylint: disable=protected-access
        for prefix in self.state_cls.dynamic_methods:
            callback = f"{prefix}_{self._get_qualified_state_name(state)}"
            func = getattr(model, callback, None)
            if isinstance(func, partial) and func.func != state.add_callback:
                state.add_callback(prefix, callback)

    # this needs to be overridden by the HSM variant to resolve names correctly
    def _get_qualified_state_name(self, state: Any) -> Any:
        return state.name

    def _locked_method(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        if self._ident.current != get_ident():
            with nested(*self.machine_context):
                return func(*args, **kwargs)
        else:
            return func(*args, **kwargs)
