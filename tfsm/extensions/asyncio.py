"""
tfsm.extensions.asyncio
------------------------------

Asynchronous state machine implementation with async-only enforcement.

This module contains asynchronous variants of the core tfsm classes:
- AsyncMachine: Async-only state machine with enforced async methods
- AsyncState: Async-only state with enforced async callbacks
- AsyncEvent: Async-only event handling
- AsyncTransition: Async-only transition execution
- HierarchicalAsyncMachine: Hierarchical async state machine

⚠️ CRITICAL DESIGN DECISION:

All async classes in this module follow an **async-only enforcement strategy**:
1. Synchronous methods inherited from parent classes are DISABLED
2. Calling a synchronous method will raise RuntimeError immediately
3. All async methods MUST be awaited in async contexts
4. Forgetting to await will create coroutine objects (silent bugs)

Example (AsyncMachine):

    ✅ CORRECT:
        machine = AsyncMachine(states=['A', 'B'], initial='A')
        await machine.advance()  # Async execution

    ❌ WRONG - returns coroutine without executing:
        machine.advance()  # BUG: Creates coroutine, doesn't execute!

    ❌ WRONG - raises RuntimeError:
        # Sync methods are disabled and will raise immediately
        # (This is intentional to prevent silent bugs)

This design deliberately violates Liskov Substitution Principle (LSP) for good reason:
- Prevents hard-to-debug bugs from missing awaits
- Forces explicit async usage
- Provides clear error messages for incorrect usage
- Maintains type safety with type: ignore[override] comments

The alternative (allowing both sync and async) would lead to:
- Silent bugs from forgotten awaits
- Confusion about when to use sync vs async
- Difficult-to-trace coroutine objects in code

For hierarchical state machines, see HierarchicalAsyncMachine.
For timeout functionality, see AsyncTimeout.

This module uses `asyncio` for concurrency. The extension `tfsm-anyio`
illustrates how they can be extended to make use of other concurrency libraries.

Note: Overriding base methods with async variants is not considered good practice
in general. However, the alternative would mean either increasing the complexity
of the base classes or copying code fragments, which would increase code
complexity and reduce maintainability. If you know a better solution, please
file an issue.
"""

# Overriding base methods of states, events and machines with async variants is not considered good practice.
# However, the alternative would mean to either increase the complexity of the base classes or copy code fragments
# and thus increase code complexity and reduce maintainability. If you know a better solution, please file an issue.
# pylint: disable=invalid-overridden-method

import asyncio
import contextvars
import copy
import inspect
import logging
import sys
import warnings
from collections import deque
from collections.abc import Callable
from functools import partial, reduce
from typing import Any, Optional

from ..core import Callback, CallbackList, Condition, Event, EventData, Machine, MachineError, State, Transition, listify
from .nesting import FunctionWrapper, HierarchicalMachine, NestedEvent, NestedState, NestedTransition, resolve_order

_LOGGER = logging.getLogger(__name__)
_LOGGER.addHandler(logging.NullHandler())


CANCELLED_MSG = "_transition"
"""A message passed to a cancelled task to indicate that the cancellation was caused by tfsm."""


class AsyncState(State):
    """Async state with async-only transition methods.

    All state transition methods (enter/exit) are async and MUST be awaited.
    """

    def enter(self, event_data: "AsyncEventData") -> None:  # type: ignore[override]
        """Synchronous version is disabled in AsyncState!

        ⚠️  Use 'await aenter(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncState.enter() is disabled. Use 'await state.aenter(...)' instead.")

    async def aenter(self, event_data: "AsyncEventData") -> None:
        """Triggered when a state is entered asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data: (AsyncEventData): The currently processed event.
        """
        _LOGGER.debug("%sEntering state %s. Processing callbacks...", event_data.machine.name, self.name)
        await event_data.machine.acallbacks(self.on_enter, event_data)
        _LOGGER.info("%sFinished processing state %s enter callbacks.", event_data.machine.name, self.name)

    def exit(self, event_data: "AsyncEventData") -> None:  # type: ignore[override]
        """Synchronous version is disabled in AsyncState!

        ⚠️  Use 'await aexit(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncState.exit() is disabled. Use 'await state.aexit(...)' instead.")

    async def aexit(self, event_data: "AsyncEventData") -> None:
        """Triggered when a state is exited asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data: (AsyncEventData): The currently processed event.
        """
        _LOGGER.debug("%sExiting state %s. Processing callbacks...", event_data.machine.name, self.name)
        await event_data.machine.acallbacks(self.on_exit, event_data)
        self._pocket = None
        _LOGGER.info("%sFinished processing state %s exit callbacks.", event_data.machine.name, self.name)


class NestedAsyncState(NestedState, AsyncState):
    """A state that allows substates. Callback execution is done asynchronously."""

    def scoped_enter(self, event_data: "AsyncEventData", scope: list[str] | None = None) -> None:  # type: ignore[override]
        """Synchronous version is disabled in NestedAsyncState!

        ⚠️  Use 'await ascoped_enter(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("NestedAsyncState.scoped_enter() is disabled. Use 'await state.ascoped_enter(...)' instead.")

    async def ascoped_enter(self, event_data: "AsyncEventData", scope: list[str] | None = None) -> None:
        """Enter state with scope asynchronously.

        ⚠️  CRITICAL: Must be awaited!
        """
        self._scope = scope or []
        await self.aenter(event_data)
        self._scope = []

    def scoped_exit(self, event_data: "AsyncEventData", scope: list[str] | None = None) -> None:  # type: ignore[override]
        """Synchronous version is disabled in NestedAsyncState!

        ⚠️  Use 'await ascoped_exit(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("NestedAsyncState.scoped_exit() is disabled. Use 'await state.ascoped_exit(...)' instead.")

    async def ascoped_exit(self, event_data: "AsyncEventData", scope: list[str] | None = None) -> None:
        """Exit state with scope asynchronously.

        ⚠️  CRITICAL: Must be awaited!
        """
        self._scope = scope or []
        await self.aexit(event_data)
        self._scope = []


class AsyncCondition(Condition):
    """Async condition with async-only check method."""

    def check(self, event_data: EventData) -> bool:
        """Synchronous version is disabled in AsyncCondition!

        ⚠️  Use 'await acheck(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncCondition.check() is disabled. Use 'await condition.acheck(...)' instead.")

    async def acheck(self, event_data: EventData) -> bool:
        """Check whether the condition passes asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data (EventData): An EventData instance to pass to the
                condition (if event sending is enabled) or to extract arguments
                from (if event sending is disabled). Also contains the data
                model attached to the current machine which is used to invoke
                the condition.
        """
        func = event_data.machine.resolve_callable(self.func, event_data)
        res = func(event_data) if event_data.machine.send_event else func(*event_data.args, **event_data.kwargs)
        if inspect.isawaitable(res):
            result = await res
            return result == self.target  # type: ignore[no-any-return]
        return res == self.target  # type: ignore[no-any-return]


class AsyncTransition(Transition):
    """Async transition with async-only execution methods.

    All transition methods (execute/_eval_conditions/_change_state) are async and MUST be awaited.
    """

    condition_cls = AsyncCondition

    def _eval_conditions(self, event_data: EventData) -> bool:
        """Synchronous version is disabled in AsyncTransition!

        ⚠️  Use 'await _aeval_conditions(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncTransition._eval_conditions() is disabled. Use 'await transition._aeval_conditions(...)' instead.")

    async def _aeval_conditions(self, event_data: EventData) -> bool:
        """Evaluate transition conditions asynchronously.

        ⚠️  CRITICAL: Must be awaited!
        """
        res = await event_data.machine.await_all(
            [partial(c.acheck, event_data) for c in self.conditions]  # type: ignore[attr-defined]
        )
        if not all(res):
            _LOGGER.debug("%sTransition condition failed: Transition halted.", event_data.machine.name)
            return False
        return True

    def execute(self, event_data: EventData) -> bool:
        """Synchronous version is disabled in AsyncTransition!

        ⚠️  Use 'await aexecute(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncTransition.execute() is disabled. Use 'await transition.aexecute(...)' instead.")

    async def aexecute(self, event_data: EventData) -> bool:
        """Execute the transition asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data (EventData): An instance of class EventData.
        Returns:
            bool: Boolean indicating whether or not the transition was successfully executed (True if successful, False if not).
        """
        _LOGGER.debug("%sInitiating transition from state %s to state %s...", event_data.machine.name, self.source, self.dest)

        await event_data.machine.acallbacks(self.prepare, event_data)
        _LOGGER.debug("%sExecuted callbacks before conditions.", event_data.machine.name)

        if not await self._aeval_conditions(event_data):
            return False

        machine = event_data.machine
        # cancel running tasks since the transition will happen
        await machine.cancel_running_transitions(event_data.model)

        await event_data.machine.acallbacks(event_data.machine.before_state_change, event_data)
        await event_data.machine.acallbacks(self.before, event_data)
        _LOGGER.debug("%sExecuted callback before transition.", event_data.machine.name)

        if self.dest is not None:  # if self.dest is None this is an internal transition with no actual state change
            await self._achange_state(event_data)

        await event_data.machine.acallbacks(self.after, event_data)
        await event_data.machine.acallbacks(event_data.machine.after_state_change, event_data)
        _LOGGER.debug("%sExecuted callback after transition.", event_data.machine.name)
        return True

    def _change_state(self, event_data: EventData) -> None:
        """Synchronous version is disabled in AsyncTransition!

        ⚠️  Use 'await _achange_state(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncTransition._change_state() is disabled. Use 'await transition._achange_state(...)' instead.")

    async def _achange_state(self, event_data: EventData) -> None:
        """Change state asynchronously.

        ⚠️  CRITICAL: Must be awaited!
        """
        if hasattr(event_data.machine, "model_graphs"):
            graph = event_data.machine.model_graphs[id(event_data.model)]
            graph.reset_styling()
            graph.set_previous_transition(self.source, self.dest)
        source_state = event_data.machine.get_state(self.source)
        await source_state.aexit(event_data)  # type: ignore[attr-defined]
        event_data.machine.set_state(self.dest, event_data.model)  # type: ignore[arg-type]
        event_data.update(getattr(event_data.model, event_data.machine.model_attribute))
        dest = event_data.machine.get_state(self.dest)  # type: ignore[arg-type]
        await dest.aenter(event_data)  # type: ignore[attr-defined]
        if dest.final:
            await event_data.machine.acallbacks(event_data.machine.on_final, event_data)


class NestedAsyncTransition(AsyncTransition, NestedTransition):
    """Representation of an asynchronous transition managed by a ``HierarchicalAsyncMachine`` instance."""

    def _change_state(self, event_data: EventData) -> None:
        """Synchronous version is disabled in NestedAsyncTransition!

        ⚠️  Use 'await _achange_state(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("NestedAsyncTransition._change_state() is disabled. Use 'await transition._achange_state(...)' instead.")

    async def _achange_state(self, event_data: EventData) -> None:
        """Change state asynchronously for nested state machines.

        ⚠️  CRITICAL: Must be awaited!
        """
        if hasattr(event_data.machine, "model_graphs"):
            graph = event_data.machine.model_graphs[id(event_data.model)]
            graph.reset_styling()
            graph.set_previous_transition(self.source, self.dest)

        state_tree, exit_partials, enter_partials = await self._aresolve_transition(event_data)  # type: ignore[arg-type]
        for func in exit_partials:
            await func()
        self._update_model(event_data, state_tree)  # type: ignore[arg-type]
        for func in enter_partials:
            await func()
        with event_data.machine():  # type: ignore[operator]
            on_final_cbs, _ = await self._afinal_check(event_data, state_tree, enter_partials)  # type: ignore[arg-type]
            for on_final_cb in on_final_cbs:
                await on_final_cb()

    def _resolve_transition(self, event_data: "AsyncEventData") -> tuple[dict[str, Any], Any, Any]:  # type: ignore[override]
        """Synchronous version is disabled in NestedAsyncTransition!

        ⚠️  Use 'await _aresolve_transition(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError(
            "NestedAsyncTransition._resolve_transition() is disabled. Use 'await transition._aresolve_transition(...)' instead."
        )

    async def _aresolve_transition(self, event_data: "AsyncEventData") -> tuple[dict[str, Any], list[Any], list[Any]]:
        """Async version of _resolve_transition.

        Creates async partial functions for scoped_enter and scoped_exit.

        ⚠️  CRITICAL: Must be awaited!
        """
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
                event_data.machine.get_state(root + state_name).ascoped_exit,  # type: ignore[attr-defined]
                event_data,
                scope + root + state_name[:-1],
            )
            for state_name in resolve_order(exit_scope)
        ]

        new_states, enter_partials = await self._aenter_nested(root, dst_name_path, scope + root, event_data)

        # we reset/clear the whole branch if it is scoped, otherwise only reset the sibling
        if exit_scope == scoped_tree:
            scoped_tree.clear()
        for new_key, value in new_states.items():
            scoped_tree[new_key] = value
            break

        return state_tree, exit_partials, enter_partials

    async def _aenter_nested(
        self, root: list[str], dest: list[str], prefix_path: list[str], event_data: "AsyncEventData"
    ) -> tuple[dict[str, Any], list[Any]]:
        """Async version of _enter_nested.

        Creates async partial functions for scoped_enter.

        ⚠️  CRITICAL: Must be awaited!
        """
        if root:
            state_name = root.pop(0)
            with event_data.machine(state_name):  # type: ignore[operator]
                return await self._aenter_nested(root, dest, prefix_path, event_data)
        elif dest:
            new_states: dict[str, Any] = {}
            state_name = dest.pop(0)
            # Capture state and prefix for closure BEFORE entering context
            prefix_copy = prefix_path
            with event_data.machine(state_name):  # type: ignore[operator]
                # Capture the scoped state's ascoped_enter method for use in wrapper
                scoped_ascoped_enter = event_data.machine.scoped.ascoped_enter
                new_states[state_name], new_enter = await self._aenter_nested([], dest, prefix_path + [state_name], event_data)

            # Create async wrapper for scoped_enter using captured method
            async def _scoped_enter_wrapper() -> None:
                """Async wrapper for scoped_enter."""
                await scoped_ascoped_enter(event_data, prefix_copy)

            # Store method reference for comparison in _afinal_check (like partial.func)
            _scoped_enter_wrapper.func = scoped_ascoped_enter  # type: ignore[attr-defined]

            enter_partials = [_scoped_enter_wrapper] + new_enter
            return new_states, enter_partials
        elif event_data.machine.scoped.initial:
            new_states_2: dict[str, Any] = {}
            enter_partials = []
            queue: list[tuple[Any, list[str], list[Any]]] = []
            prefix = prefix_path
            scoped_tree: dict[str, Any] = new_states_2
            initial_names = [i.name if hasattr(i, "name") else i for i in listify(event_data.machine.scoped.initial)]
            initial_states = [event_data.machine.scoped.states[n] for n in initial_names]
            while True:
                event_data.scope = prefix  # type: ignore[attr-defined]
                for state in initial_states:
                    # Capture state's ascoped_enter method and prefix for closure
                    state_ascoped_enter = state.ascoped_enter
                    prefix_copy = prefix  # Capture prefix for closure

                    async def _make_wrapper(ascoped_enter: Any = state_ascoped_enter, pref: list[str] = prefix_copy) -> None:
                        """Async wrapper for scoped_enter."""
                        await ascoped_enter(event_data, pref)

                    # Store method reference for comparison in _afinal_check (like partial.func)
                    _make_wrapper.func = state_ascoped_enter  # type: ignore[attr-defined]

                    enter_partials.append(_make_wrapper)
                    scoped_tree[state.name] = {}
                    if state.initial:
                        queue.append((
                            scoped_tree[state.name],
                            prefix + [state.name],
                            [state.states[i.name] if hasattr(i, "name") else state.states[i] for i in listify(state.initial)],
                        ))
                if not queue:
                    break
                scoped_tree, prefix, initial_states = queue.pop(0)

            return new_states_2, enter_partials
        else:
            return {}, []

    async def _afinal_check(
        self, event_data: "AsyncEventData", state_tree: dict[str, Any], enter_partials: list[Any]
    ) -> tuple[list[Any], bool]:
        """Async version of _final_check.

        ⚠️  CRITICAL: Must be awaited!
        """
        on_final_cbs = []
        is_final = False
        # processes states with children
        if state_tree:
            all_children_final = True
            # For parallel states, we need to stay in the parent context
            # Don't use asyncio.gather here as it would execute concurrently
            # Instead, process sequentially to maintain proper scope
            for state in state_tree:
                child_cbs, child_final = await self._afinal_check_nested(state, event_data, state_tree[state], enter_partials)
                # if one child is not considered final, processing can stop
                if not child_final:
                    all_children_final = False
                    # if one child has recently transitioned to a final state, we need to update all parents
                on_final_cbs.extend(child_cbs)
            # if and only if all other children are also in a final state and a child has recently reached a final
            # state OR the scoped state has just been entered, trigger callbacks
            if all_children_final:
                scoped_state = event_data.machine.scoped
                scoped_entered = any(
                    hasattr(scoped_state, "ascoped_enter") and scoped_state.ascoped_enter == getattr(part, "func", None)
                    for part in enter_partials
                )
                if on_final_cbs or scoped_entered:
                    # Use scoped state's on_final (which may be machine.on_final at top level)
                    if hasattr(scoped_state, "on_final") and scoped_state.on_final:
                        on_final_cbs.append(partial(event_data.machine.acallbacks, scoped_state.on_final, event_data))
                is_final = True
        # if a state is a leaf state OR has children not in a final state
        elif getattr(event_data.machine.scoped, "final", False):
            # if the state itself is considered final and has recently been entered trigger callbacks
            # thus, a state with non-final children may still trigger callbacks if itself is considered final
            scoped_state = event_data.machine.scoped
            scoped_entered = any(
                hasattr(scoped_state, "ascoped_enter") and scoped_state.ascoped_enter == getattr(part, "func", None)
                for part in enter_partials
            )
            if scoped_entered:
                # Use scoped state's on_final (even if empty, like original code)
                if hasattr(scoped_state, "on_final"):
                    on_final_cbs.append(partial(event_data.machine.acallbacks, scoped_state.on_final, event_data))
            is_final = True
        return on_final_cbs, is_final

    async def _afinal_check_nested(
        self, state: str, event_data: "AsyncEventData", state_tree: dict[str, Any], enter_partials: list[Any]
    ) -> tuple[list[Any], bool]:
        """Async version of _final_check_nested.

        ⚠️  CRITICAL: Must be awaited!
        """
        # Check if 'state' is a top-level state (can be accessed directly)
        # or a nested state (needs parent context)
        try:
            # Try to get the state - if it's a top-level state, this will succeed
            state_obj = event_data.machine.states.get(state, None)
            if state_obj is not None:
                # Top-level state, use normal context switching
                with event_data.machine(state):  # type: ignore[operator]
                    return await self._afinal_check(event_data, state_tree, enter_partials)
            else:
                # Nested state (child of parallel state), stay in current scope
                return await self._afinal_check(event_data, state_tree, enter_partials)
        except Exception:
            # If anything goes wrong, stay in current scope
            return await self._afinal_check(event_data, state_tree, enter_partials)


class AsyncEventData(EventData):
    """A redefinition of the base EventData intended to easy type checking."""


class AsyncEvent(Event):
    """Async event with async-only execution methods.

    All event processing methods (trigger/_trigger/_process) are async and MUST be awaited.
    """

    def trigger(self, model: Any, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in AsyncEvent!

        ⚠️  Use 'await atrigger(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncEvent.trigger() is disabled. Use 'await event.atrigger(...)' instead.")

    async def atrigger(self, model: Any, *args: Any, **kwargs: Any) -> bool:
        """Serially execute all transitions that match the current state asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            args and kwargs: Optional positional or named arguments that will
                be passed onto the EventData object, enabling arbitrary state
                information to be passed on to downstream triggered functions.
        Returns:
            bool: Boolean indicating whether or not a transition was successfully executed (True if successful, False if not).
        """
        func = partial(self._atrigger, EventData(None, self, self.machine, model, args=args, kwargs=kwargs))
        return await self.machine.process_context(func, model)  # type: ignore[no-any-return]

    def _trigger(self, event_data: EventData) -> bool:
        """Synchronous version is disabled in AsyncEvent!

        ⚠️  Use 'await _atrigger(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncEvent._trigger() is disabled. Use 'await event._atrigger(...)' instead.")

    async def _atrigger(self, event_data: EventData) -> bool:
        """Internal trigger function (async).

        ⚠️  CRITICAL: Must be awaited!
        """
        event_data.state = self.machine.get_state(getattr(event_data.model, self.machine.model_attribute))
        try:
            if self._is_valid_source(event_data.state):
                await self._aprocess(event_data)
        except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
            _LOGGER.error(
                "%sException was raised while processing the trigger '%s': %s", self.machine.name, event_data.event.name, repr(err)
            )
            event_data.error = err  # type: ignore[assignment]
            if self.machine.on_exception:
                await self.machine.acallbacks(self.machine.on_exception, event_data)
            else:
                raise
        finally:
            try:
                await self.machine.acallbacks(self.machine.finalize_event, event_data)
                _LOGGER.debug("%sExecuted machine finalize callbacks", self.machine.name)
            except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
                _LOGGER.error("%sWhile executing finalize callbacks a %s occurred: %s.", self.machine.name, type(err).__name__, str(err))
        return event_data.result

    def _process(self, event_data: EventData) -> None:
        """Synchronous version is disabled in AsyncEvent!

        ⚠️  Use 'await _aprocess(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncEvent._process() is disabled. Use 'await event._aprocess(...)' instead.")

    async def _aprocess(self, event_data: EventData) -> None:
        """Process event transitions (async).

        ⚠️  CRITICAL: Must be awaited!
        """
        await self.machine.acallbacks(self.machine.prepare_event, event_data)
        _LOGGER.debug("%sExecuted machine preparation callbacks before conditions.", self.machine.name)
        for trans in self.transitions[event_data.state.name]:  # type: ignore[union-attr]
            event_data.transition = trans
            event_data.result = await trans.aexecute(event_data)  # type: ignore[attr-defined]
            if event_data.result:
                break


class NestedAsyncEvent(NestedEvent):
    """A collection of transitions assigned to the same trigger.

    This Event requires a (subclass of) `HierarchicalAsyncMachine`.
    """

    def trigger_nested(self, event_data: EventData) -> bool:
        """Synchronous version is disabled in NestedAsyncEvent!

        ⚠️  Use 'await atrigger_nested(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("NestedAsyncEvent.trigger_nested() is disabled. Use 'await event.atrigger_nested(...)' instead.")

    async def atrigger_nested(self, event_data: EventData) -> bool:
        """Serially execute all transitions that match the current state asynchronously.

        ⚠️  CRITICAL: Must be awaited!

        NOTE: This should only be called by HierarchicalAsyncMachine instances.

        Args:
            event_data (AsyncEventData): The currently processed event.
        Returns:
            bool: Boolean indicating whether or not a transition was successfully executed (True if successful, False if not).
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
                event_data.source_name = state_name  # type: ignore[attr-defined]
                event_data.source_path = copy.copy(state_path)  # type: ignore[attr-defined]
                await self._aprocess(event_data)
                if event_data.result:
                    elems = state_path
                    while elems:
                        done.add(machine.state_cls.separator.join(elems))  # type: ignore[attr-defined]
                        elems.pop()
        return event_data.result

    def _process(self, event_data: EventData) -> None:
        """Synchronous version is disabled in NestedAsyncEvent!

        ⚠️  Use 'await _aprocess(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("NestedAsyncEvent._process() is disabled. Use 'await event._aprocess(...)' instead.")

    async def _aprocess(self, event_data: EventData) -> None:
        """Process event transitions for nested state machines (async).

        ⚠️  CRITICAL: Must be awaited!
        """
        machine = event_data.machine
        await machine.acallbacks(event_data.machine.prepare_event, event_data)
        _LOGGER.debug("%sExecuted machine preparation callbacks before conditions.", machine.name)

        for trans in self.transitions[event_data.source_name]:  # type: ignore[attr-defined]
            event_data.transition = trans
            event_data.result = await trans.aexecute(event_data)  # type: ignore[attr-defined]
            if event_data.result:
                break


class AsyncMachine(Machine):
    """Asynchronous state machine with enforced async-only methods.

    AsyncMachine is a pure async state machine implementation that enforces
    async usage by disabling all synchronous methods inherited from Machine.

    ⚠️ ASYNC-ONLY ENFORCEMENT:

    This class deliberately violates Liskov Substitution Principle (LSP) to prevent
    hard-to-debug bugs. All synchronous methods from Machine are DISABLED and will
    raise RuntimeError if called.

    CRITICAL RULES:
        1. All methods MUST be awaited
        2. Forgetting await creates coroutine objects (silent bugs)
        3. Sync methods raise RuntimeError immediately
        4. Use only in async contexts

    Example:
        >>> # ✅ CORRECT - Async usage
        >>> machine = AsyncMachine(states=['A', 'B'], initial='A')
        >>> await machine.advance()  # Returns bool, executes transition

        >>> # ❌ WRONG - Missing await (creates coroutine)
        >>> machine.advance()  # Returns <coroutine>, doesn't execute!

        >>> # ❌ WRONG - Sync method raises RuntimeError
        >>> # (sync methods are intentionally disabled)

    Attributes:
        states (OrderedDict): Collection of all registered states.
        events (dict): Collection of events ordered by trigger/event.
        models (list): List of models attached to the machine.
        initial (str): Name of the initial state for new models.
        prepare_event (list): Callbacks executed when an event is triggered (async).
        before_state_change (list): Callbacks executed after condition checks but before transition (async).
        after_state_change (list): Callbacks executed after the transition (async).
        finalize_event (list): Callbacks executed after all events have been processed (async).
        on_exception: A callable called when an event raises an exception (async).
        queued (bool or str): Whether events should be executed immediately or sequentially.
        send_event (bool): When True, arguments are wrapped in EventData objects.
        auto_transitions (bool):  When True (default), auto-generates to_{state}() methods.
        ignore_invalid_triggers (bool): When True, invalid triggers are silently ignored.
        name (str): Name of the machine instance for log messages.

    Type Safety:
        This class uses `# type: ignore[override]` comments to intentionally suppress
        mypy errors about LSP violations. This is documented and intentional.

    Performance:
        AsyncMachine uses asyncio.gather() for parallel callback execution, which
        may have different performance characteristics than the synchronous Machine.
    """

    state_cls = AsyncState
    transition_cls = AsyncTransition
    event_cls = AsyncEvent
    async_tasks: dict[int, list["asyncio.Task[Any]"]] = {}
    protected_tasks: list["asyncio.Task[Any]"] = []
    current_context: contextvars.ContextVar[Optional["asyncio.Task[Any]"]] = contextvars.ContextVar("current_context", default=None)

    def __init__(
        self,
        model: Any = Machine.self_literal,
        states: list[Any] | None = None,
        initial: str = "initial",
        transitions: list[Any] | None = None,
        send_event: bool = False,
        auto_transitions: bool = True,
        ordered_transitions: bool = False,
        ignore_invalid_triggers: bool | None = None,
        before_state_change: Callback | None = None,
        after_state_change: Callback | None = None,
        name: str | None = None,
        queued: bool | str = False,
        prepare_event: Callback | None = None,
        finalize_event: Callback | None = None,
        model_attribute: str = "state",
        model_override: bool = False,
        on_exception: Callback | None = None,
        on_final: Callback | None = None,
        **kwargs: Any,
    ) -> None:

        super().__init__(
            model=None,
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
            queued=bool(queued),
            prepare_event=prepare_event,
            finalize_event=finalize_event,
            model_attribute=model_attribute,
            model_override=model_override,
            on_exception=on_exception,
            on_final=on_final,
            **kwargs,
        )

        self._transition_queue_dict: dict[int, deque[partial[Any]]] = _DictionaryMock(self._transition_queue) if queued is True else {}
        self._queued: bool | str = queued  # type: ignore[assignment]
        for model in listify(model):
            self.add_model(model)

    def add_model(self, model: Any, initial: str | None = None) -> None:  # type: ignore[override]
        """Add a model to the async machine.

        Overrides Machine.add_model to bind async versions of trigger and may_trigger methods.
        """
        if model is Machine.self_literal:
            model = self

        if initial is None:
            if self.initial is None:
                raise ValueError("No initial state configured for machine, must specify when adding model.")
            initial = self.initial  # type: ignore[assignment]

        for mod in listify(model):
            mod = self if mod is self.self_literal else mod
            if mod not in self.models:
                # Bind async versions of trigger and may_trigger
                async def _trigger_wrapper(trigger_name: str, *args: Any, model: Any = mod, **kwargs: Any) -> bool:
                    """Async wrapper for generic trigger."""
                    return await self._aget_trigger(model, trigger_name, *args, **kwargs)

                async def _may_trigger_wrapper(trigger_name: str, *args: Any, model: Any = mod, **kwargs: Any) -> bool:
                    """Async wrapper for may_trigger."""
                    return await self._acan_trigger(model, trigger_name, *args, **kwargs)

                self._checked_assignment(mod, "trigger", _trigger_wrapper)
                self._checked_assignment(mod, "may_trigger", _may_trigger_wrapper)

                for trigger in self.events:
                    self._add_trigger_to_model(trigger, mod)

                for state in self.states.values():
                    self._add_model_to_state(state, mod)

                self.set_state(initial, model=mod)  # type: ignore[arg-type]
                self.models.append(mod)

        if self.has_queue == "model":  # type: ignore[comparison-overlap]
            for mod in listify(model):
                self._transition_queue_dict[id(self) if mod is self.self_literal else id(mod)] = deque()

    def _add_trigger_to_model(self, trigger: str, model: Any) -> None:
        """Add an async trigger wrapper to the model.

        Overrides Machine._add_trigger_to_model to create async trigger functions.
        The wrapper will call event.atrigger() instead of event.trigger().

        Args:
            trigger: Name of the trigger/event
            model: Model to add the trigger to
        """

        async def _trigger_wrapper(*args: Any, **kwargs: Any) -> bool:
            """Async wrapper that calls the event's atrigger method."""
            return await self.events[trigger].atrigger(model, *args, **kwargs)  # type: ignore[attr-defined, no-any-return]

        self._checked_assignment(model, trigger, _trigger_wrapper)
        self._add_may_transition_func_for_trigger(trigger, model)

    def _add_may_transition_func_for_trigger(self, trigger: str, model: Any) -> None:
        """Add an async may_transition wrapper to the model.

        Overrides Machine._add_may_transition_func_for_trigger to create async functions.
        The wrapper will call _acan_trigger() instead of _can_trigger().

        Args:
            trigger: Name of the trigger/event
            model: Model to add the may_transition function to
        """

        async def _may_trigger_wrapper(*args: Any, **kwargs: Any) -> bool:
            """Async wrapper that calls _acan_trigger."""
            return await self._acan_trigger(model, trigger, *args, **kwargs)

        self._checked_assignment(model, "may_%s" % trigger, _may_trigger_wrapper)

    def _get_trigger(self, model: Any, trigger_name: str, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await _aget_trigger(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine._get_trigger() is disabled. Use 'await machine._aget_trigger(...)' instead.")

    async def _aget_trigger(self, model: Any, trigger_name: str, *args: Any, **kwargs: Any) -> bool:
        """Async version of Machine._get_trigger.

        Convenience function added to models to trigger events by name.
        Calls event.atrigger() instead of event.trigger().

        ⚠️  CRITICAL: Must be awaited!

        Args:
            model: Model with assigned event trigger
            trigger_name: Name of the trigger to be called
            *args: Variable length argument list passed to the triggered event
            **kwargs: Arbitrary keyword arguments passed to the triggered event

        Returns:
            bool: True if a transition has been conducted or the trigger event has been queued
        """
        try:
            event = self.events[trigger_name]
        except KeyError:
            state = self.get_model_state(model)
            ignore = state.ignore_invalid_triggers if state.ignore_invalid_triggers is not None else self.ignore_invalid_triggers
            if not ignore:
                raise AttributeError("Do not know event named '%s'." % trigger_name)
            return False
        return await event.atrigger(model, *args, **kwargs)  # type: ignore[attr-defined, no-any-return]

    def dispatch(self, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await adispatch(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine.dispatch() is disabled. Use 'await machine.adispatch(...)' instead.")

    async def adispatch(self, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Trigger an event on all models assigned to the machine asynchronously.

        ⚠️  CRITICAL:
            This is an async method and MUST be awaited:
                ✅ await machine.adispatch('event')
                ❌ machine.adispatch('event')  # BUG: Creates coroutine, won't execute!

        If you don't await, you'll get a coroutine object instead of the result.

        Args:
            trigger (str): Event name
            *args (list): List of arguments passed to the event trigger
            **kwargs (dict): Dictionary of keyword arguments passed to the event trigger
        Returns:
            bool: The truth value of all triggers combined with AND
        """
        results = await self.await_all([partial(getattr(model, trigger), *args, **kwargs) for model in self.models])
        return all(results)

    def callbacks(self, funcs: CallbackList, event_data: EventData) -> None:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await acallbacks(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine.callbacks() is disabled. Use 'await machine.acallbacks(...)' instead.")

    async def acallbacks(self, funcs: CallbackList, event_data: EventData) -> None:
        """Triggers a list of callbacks asynchronously.

        ⚠️  CRITICAL: Must be awaited: await machine.acallbacks(...)
        """
        await self.await_all([partial(event_data.machine.acallback, func, event_data) for func in funcs])

    def callback(self, func: str | Callback, event_data: EventData) -> None:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await acallback(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine.callback() is disabled. Use 'await machine.acallback(...)' instead.")

    async def acallback(self, func: str | Callback, event_data: EventData) -> None:
        """Trigger a callback function asynchronously.

        ⚠️  CRITICAL: Must be awaited: await machine.acallback(...)

        Automatically awaits awaitable results from callbacks.

        Args:
            func (string, callable): The callback function.
                1. First, if the func is callable, just call it
                2. Second, we try to import string assuming it is a path to a func
                3. Fallback to a model attribute
            event_data (EventData): An EventData instance to pass to the
                callback (if event sending is enabled) or to extract arguments
                from (if event sending is disabled).
        """
        func = self.resolve_callable(func, event_data)
        res = func(event_data) if self.send_event else func(*event_data.args, **event_data.kwargs)
        if inspect.isawaitable(res):
            await res

    @staticmethod
    async def await_all(callables: list[Callable[[], Any]]) -> list[Any]:
        """
        Executes callables without parameters in parallel and collects their results.
        Args:
            callables (list): A list of callable functions

        Returns:
            list: A list of results. Using asyncio the list will be in the same order as the passed callables.
        """
        return await asyncio.gather(*[func() for func in callables])

    async def switch_model_context(self, model: Any) -> None:
        warnings.warn(
            "Please replace 'AsyncMachine.switch_model_context' with 'AsyncMachine.cancel_running_transitions'.",
            category=DeprecationWarning,
        )
        await self.cancel_running_transitions(model)

    async def cancel_running_transitions(self, model: Any, msg: str | None = None) -> None:
        """
        This method is called by an `AsyncTransition` when all conditional tests have passed
        and the transition will happen. This requires already running tasks to be cancelled.
        Args:
            model (object): The currently processed model
            msg (str): Optional message to pass to a running task's cancel request (deprecated).
        """
        if msg is not None:
            warnings.warn(
                "When you call cancel_running_transitions with a custom message "
                "tfsm will re-raise all raised CancelledError. "
                "Make sure to catch them in your code. "
                "The parameter 'msg' will likely be removed in a future release.",
                category=DeprecationWarning,
            )
        for running_task in self.async_tasks.get(id(model), []):
            if self.current_context.get() == running_task or running_task in self.protected_tasks:
                continue
            if running_task.done() is False:
                _LOGGER.debug("Cancel running tasks...")
                running_task.cancel(msg or CANCELLED_MSG)

    async def process_context(self, func: partial[Any], model: Any) -> bool:
        """
        This function is called by an `AsyncEvent` to make callbacks processed in Event._trigger cancellable.
        Using asyncio this will result in a try-catch block catching CancelledEvents.
        Args:
            func (partial): The partial of Event._trigger with all parameters already assigned
            model (object): The currently processed model

        Returns:
            bool: returns the success state of the triggered event
        """
        if self.current_context.get() is None:
            token = self.current_context.set(asyncio.current_task())
            if id(model) in self.async_tasks:
                self.async_tasks[id(model)].append(asyncio.current_task())  # type: ignore[arg-type]
            else:
                self.async_tasks[id(model)] = [asyncio.current_task()]  # type: ignore[list-item]
            try:
                res = await self._aprocess(func, model)
            except asyncio.CancelledError as err:
                # raise CancelledError only if the task was not cancelled by internal processes
                # we indicate internal cancellation by passing CANCELLED_MSG to cancel()
                if CANCELLED_MSG not in err.args and sys.version_info >= (3, 11):
                    _LOGGER.debug("%sExternal cancellation of task. Raise CancelledError...", self.name)
                    raise
                res = False
            finally:
                self.async_tasks[id(model)].remove(asyncio.current_task())  # type: ignore[arg-type]
                self.current_context.reset(token)
                if len(self.async_tasks[id(model)]) == 0:
                    del self.async_tasks[id(model)]
        else:
            res = await self._aprocess(func, model)
        return res

    def remove_model(self, model: Any) -> None:
        """Remove a model from the state machine. The model will still contain all previously added triggers
        and callbacks, but will not receive updates when states or tfsm are added to the Machine.
        If an event queue is used, all queued events of that model will be removed."""
        models = listify(model)
        if self.has_queue == "model":  # type: ignore[comparison-overlap]
            for mod in models:
                del self._transition_queue_dict[id(mod)]
                self.models.remove(mod)
        else:
            for mod in models:
                self.models.remove(mod)
        if len(self._transition_queue) > 0:
            queue = self._transition_queue
            new_queue = [queue.popleft()] + [e for e in queue if e.args[0].model not in models]
            self._transition_queue.clear()
            self._transition_queue.extend(new_queue)

    def _can_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await _acan_trigger(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine._can_trigger() is disabled. Use 'await machine._acan_trigger(...)' instead.")

    async def _acan_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Check if an event can be triggered asynchronously.

        ⚠️  CRITICAL: Must be awaited.
        """
        state = self.get_model_state(model)
        event_data = AsyncEventData(state, AsyncEvent(name=trigger, machine=self), self, model, args, kwargs)

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
                    await self.acallbacks(self.prepare_event, event_data)
                    await self.acallbacks(transition.prepare, event_data)
                    if all(await self.await_all([partial(c.acheck, event_data) for c in transition.conditions])):  # type: ignore[attr-defined]
                        return True
                except BaseException as err:  # pylint: disable=broad-except
                    event_data.error = err  # type: ignore[assignment]
                    if self.on_exception:
                        await self.acallbacks(self.on_exception, event_data)
                    else:
                        raise
        return False

    def _process(self, trigger: Callable[[], bool]) -> bool:
        """Synchronous version is disabled in AsyncMachine!

        ⚠️  Use 'await _aprocess(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncMachine._process() is disabled. Use 'await machine._aprocess(...)' instead.")

    async def _aprocess(self, trigger: partial[Any], model: Any) -> bool:
        # default processing
        if not self.has_queue:
            if not self._transition_queue:
                # if trigger raises an Error, it has to be handled by the Machine.process caller
                return await trigger()  # type: ignore[no-any-return]
            raise MachineError("Attempt to process events synchronously while transition queue is not empty!")

        self._transition_queue_dict[id(model)].append(trigger)
        # another entry in the queue implies a running transition; skip immediate execution
        if len(self._transition_queue_dict[id(model)]) > 1:
            return True

        while self._transition_queue_dict[id(model)]:
            try:
                await self._transition_queue_dict[id(model)][0]()
            except BaseException:
                # if a transition raises an exception, clear queue and delegate exception handling
                self._transition_queue_dict[id(model)].clear()
                raise
            try:
                self._transition_queue_dict[id(model)].popleft()
            except KeyError:
                return True
        return True


class HierarchicalAsyncMachine(HierarchicalMachine, AsyncMachine):
    """Asynchronous variant of tfsm.extensions.nesting.HierarchicalMachine.
    An asynchronous hierarchical machine REQUIRES AsyncNestedStates, AsyncNestedEvent and AsyncNestedTransitions
    (or any subclass of it) to operate.
    """

    state_cls = NestedAsyncState
    transition_cls = NestedAsyncTransition
    event_cls = NestedAsyncEvent  # type: ignore[assignment]

    def _add_trigger_to_model(self, trigger: str, model: Any) -> None:
        """Add an async trigger wrapper to the model for hierarchical machines.

        Overrides HierarchicalMachine._add_trigger_to_model to create async trigger functions.
        The wrapper will call atrigger_event() instead of trigger_event().

        Args:
            trigger: Name of the trigger/event
            model: Model to add the trigger to
        """

        async def _trigger_wrapper(*args: Any, **kwargs: Any) -> bool:
            """Async wrapper that calls the machine's atrigger_event method."""
            return await self.atrigger_event(model, trigger, *args, **kwargs)

        self._add_may_transition_func_for_trigger(trigger, model)
        # FunctionWrappers are only necessary if a custom separator is used
        if trigger.startswith("to_") and self.state_cls.separator != "_":
            path = trigger.removeprefix("to_").split(self.state_cls.separator)
            if hasattr(model, "to_" + path[0]):
                # add path to existing function wrapper
                getattr(model, "to_" + path[0]).add(_trigger_wrapper, path[1:])
            else:
                # create a new function wrapper
                self._checked_assignment(model, "to_" + path[0], FunctionWrapper(_trigger_wrapper))
        else:
            self._checked_assignment(model, trigger, _trigger_wrapper)

    def trigger_event(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in HierarchicalAsyncMachine!

        ⚠️  Use 'await atrigger_event(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("HierarchicalAsyncMachine.trigger_event() is disabled. Use 'await machine.atrigger_event(...)' instead.")

    async def atrigger_event(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
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
        event_data = AsyncEventData(state=None, event=None, machine=self, model=model, args=args, kwargs=kwargs)  # type: ignore[arg-type]
        event_data.result = None  # type: ignore[assignment]

        return await self.process_context(partial(self._atrigger_event, event_data, trigger), model)

    def _trigger_event(self, event_data: "AsyncEventData", trigger: str) -> bool:  # type: ignore[override]
        """Synchronous version is disabled in HierarchicalAsyncMachine!

        ⚠️  Use 'await _atrigger_event(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("HierarchicalAsyncMachine._trigger_event() is disabled. Use 'await machine._atrigger_event(...)' instead.")

    async def _atrigger_event(self, event_data: "AsyncEventData", trigger: str) -> bool:
        """Async version of _trigger_event.

        ⚠️  CRITICAL: Must be awaited!
        """
        try:
            with self():
                res = await self._atrigger_event_nested(event_data, trigger, None)
            event_data.result = self._check_event_result(res, event_data.model, trigger)
        except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
            event_data.error = err  # type: ignore[assignment]
            if self.on_exception:
                await self.acallbacks(self.on_exception, event_data)
            else:
                raise
        finally:
            try:
                await self.acallbacks(self.finalize_event, event_data)
                _LOGGER.debug("%sExecuted machine finalize callbacks", self.name)
            except BaseException as err:  # pylint: disable=broad-except; Exception will be handled elsewhere
                _LOGGER.error("%sWhile executing finalize callbacks a %s occurred: %s.", self.name, type(err).__name__, str(err))
        return event_data.result

    def _trigger_event_nested(self, event_data: "AsyncEventData", _trigger: str, _state_tree: dict[str, Any] | None) -> bool:  # type: ignore[override]
        """Synchronous version is disabled in HierarchicalAsyncMachine!

        ⚠️  Use 'await _atrigger_event_nested(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError(
            "HierarchicalAsyncMachine._trigger_event_nested() is disabled. Use 'await machine._atrigger_event_nested(...)' instead."
        )

    async def _atrigger_event_nested(self, event_data: "AsyncEventData", _trigger: str, _state_tree: dict[str, Any] | None) -> bool | None:
        """Async version of _trigger_event_nested.

        ⚠️  CRITICAL: Must be awaited!
        """
        model = event_data.model
        if _state_tree is None:
            _state_tree = self.build_state_tree(
                listify(getattr(model, self.model_attribute)),  # type: ignore[arg-type]
                self.state_cls.separator,
            )
        res: dict[str, bool | None] = {}
        for key, value in _state_tree.items():
            if value:
                with self(key):
                    tmp = await self._atrigger_event_nested(event_data, _trigger, value)
                    if tmp is not None:
                        res[key] = tmp
            if not res.get(key, None) and _trigger in self.events:
                tmp = await self.events[_trigger].atrigger_nested(event_data)  # type: ignore[attr-defined]
                if tmp is not None:
                    res[key] = tmp
        return None if not res or all(v is None for v in res.values()) else any(res.values())

    def _can_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in HierarchicalAsyncMachine!

        ⚠️  Use 'await _acan_trigger(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("HierarchicalAsyncMachine._can_trigger() is disabled. Use 'await machine._acan_trigger(...)' instead.")

    async def _acan_trigger(self, model: Any, trigger: str, *args: Any, **kwargs: Any) -> bool:
        """Async version of _can_trigger.

        ⚠️  CRITICAL: Must be awaited!
        """
        state_tree = self.build_state_tree(getattr(model, self.model_attribute), self.state_cls.separator)
        ordered_states = resolve_order(state_tree)
        for state_path in ordered_states:
            with self():
                return await self._acan_trigger_nested(model, trigger, state_path, *args, **kwargs)
        return False

    def _can_trigger_nested(self, model: Any, trigger: str, path: list[str], *args: Any, **kwargs: Any) -> bool:
        """Synchronous version is disabled in HierarchicalAsyncMachine!

        ⚠️  Use 'await _acan_trigger_nested(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError(
            "HierarchicalAsyncMachine._can_trigger_nested() is disabled. Use 'await machine._acan_trigger_nested(...)' instead."
        )

    async def _acan_trigger_nested(self, model: Any, trigger: str, path: list[str], *args: Any, **kwargs: Any) -> bool:
        """Async version of _can_trigger_nested.

        ⚠️  CRITICAL: Must be awaited!
        """
        if trigger in self.events:
            source_path = copy.copy(path)
            while source_path:
                event_data = AsyncEventData(self.get_state(source_path), AsyncEvent(name=trigger, machine=self), self, model, args, kwargs)
                state_name = self.state_cls.separator.join(source_path)
                for transition in self.events[trigger].transitions.get(state_name, []):
                    try:
                        _ = self.get_state(transition.dest) if transition.dest is not None else transition.source
                    except ValueError:
                        continue
                    event_data.transition = transition
                    try:
                        await self.acallbacks(self.prepare_event, event_data)
                        await self.acallbacks(transition.prepare, event_data)
                        if all(await self.await_all([partial(c.acheck, event_data) for c in transition.conditions])):  # type: ignore[attr-defined]
                            return True
                    except BaseException as err:  # pylint: disable=broad-except
                        event_data.error = err  # type: ignore[assignment]
                        if self.on_exception:
                            await self.acallbacks(self.on_exception, event_data)
                        else:
                            raise
                source_path.pop(-1)
        if path:
            with self(path.pop(0)):
                return await self._acan_trigger_nested(model, trigger, path, *args, **kwargs)
        return False


class AsyncTimeout(AsyncState):
    """
    Adds timeout functionality to an asynchronous state. Timeouts are handled model-specific.

    Attributes:
        timeout (float): Seconds after which a timeout function should be
                         called.
        on_timeout (list): Functions to call when a timeout is triggered.
        runner (dict): Keeps track of running timeout tasks to cancel when a state is exited.
    """

    dynamic_methods = ["on_timeout"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Args:
            **kwargs: If kwargs contain 'timeout', assign the float value to
                self.timeout. If timeout is set, 'on_timeout' needs to be
                passed with kwargs as well or an AttributeError will be thrown
                if timeout is not passed or equal 0.
        """
        self.timeout: float = kwargs.pop("timeout", 0)
        self._on_timeout: CallbackList | None = None
        if self.timeout > 0:
            try:
                self.on_timeout = kwargs.pop("on_timeout")
            except KeyError:
                raise AttributeError("Timeout state requires 'on_timeout' when timeout is set.") from None
        else:
            self.on_timeout = kwargs.pop("on_timeout", None)
        self.runner: dict[int, asyncio.Task[Any]] = {}
        super().__init__(*args, **kwargs)

    def enter(self, event_data: "AsyncEventData") -> None:  # type: ignore[override]
        """Synchronous version is disabled in AsyncTimeout!

        ⚠️  Use 'await aenter(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncTimeout.enter() is disabled. Use 'await state.aenter(...)' instead.")

    async def aenter(self, event_data: "AsyncEventData") -> None:
        """Enter timeout state asynchronously.

        Extends `AsyncState.aenter` by starting a timeout timer for
        the current model when the state is entered and self.timeout is larger than 0.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data (EventData): events representing the currently processed event.
        """
        if self.timeout > 0:
            self.runner[id(event_data.model)] = self.acreate_timer(event_data)
        await super().aenter(event_data)

    def exit(self, event_data: "AsyncEventData") -> None:  # type: ignore[override]
        """Synchronous version is disabled in AsyncTimeout!

        ⚠️  Use 'await aexit(...)' instead.

        Raises:
            RuntimeError: Always raised when called
        """
        raise RuntimeError("AsyncTimeout.exit() is disabled. Use 'await state.aexit(...)' instead.")

    async def aexit(self, event_data: "AsyncEventData") -> None:
        """Exit timeout state asynchronously.

        Cancels running timeout tasks stored in `self.runner` first (when not empty) before
        calling further exit callbacks.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data (EventData): Data representing the currently processed event.
        """
        timer_task = self.runner.get(id(event_data.model), None)
        if timer_task is not None and not timer_task.done():
            timer_task.cancel()
        await super().aexit(event_data)

    def acreate_timer(self, event_data: "AsyncEventData") -> "asyncio.Task[Any]":
        """
        Creates and returns a running timer. Shields self._aprocess_timeout to prevent cancellation when
        transitioning away from the current state (which cancels the timer) while processing timeout callbacks.

        ⚠️  CRITICAL: Must be awaited!

        Args:
            event_data (EventData): Data representing the currently processed event.

        Returns:
            asyncio.Task: A running timer with a cancel method
        """

        async def _timeout() -> None:
            await asyncio.sleep(self.timeout)
            await asyncio.shield(self._aprocess_timeout(event_data))

        return asyncio.create_task(_timeout())

    async def _aprocess_timeout(self, event_data: "AsyncEventData") -> None:
        """Process timeout callbacks asynchronously.

        ⚠️  CRITICAL: Must be awaited!
        """
        _LOGGER.debug("%sTimeout state %s. Processing callbacks...", event_data.machine.name, self.name)
        event_data = AsyncEventData(
            event_data.state, AsyncEvent("timeout", event_data.machine), event_data.machine, event_data.model, args=tuple(), kwargs={}
        )
        token = AsyncMachine.current_context.set(None)
        try:
            await event_data.machine.acallbacks(self.on_timeout, event_data)
        except BaseException as err:
            _LOGGER.warning("%sException raised while processing timeout!", event_data.machine.name)
            event_data.error = err  # type: ignore[assignment]
            try:
                if event_data.machine.on_exception:
                    await event_data.machine.acallbacks(event_data.machine.on_exception, event_data)
                else:
                    raise
            except BaseException as err2:  # pylint: disable=broad-except
                _LOGGER.error(
                    "%sHandling timeout exception '%s' caused another exception: %s. Cancel running transitions...",
                    event_data.machine.name,
                    repr(err),
                    repr(err2),
                )
                await event_data.machine.cancel_running_transitions(event_data.model)
        finally:
            AsyncMachine.current_context.reset(token)
        _LOGGER.info("%sTimeout state %s processed.", event_data.machine.name, self.name)

    @property
    def on_timeout(self) -> CallbackList:
        """
        List of strings and callables to be called when the state timeouts.
        """
        return self._on_timeout  # type: ignore[return-value]

    @on_timeout.setter
    def on_timeout(self, value: CallbackList | None) -> None:
        """Listifies passed values and assigns them to on_timeout."""
        self._on_timeout = listify(value)  # type: ignore[assignment]


class _DictionaryMock(dict):  # type: ignore[type-arg]
    def __init__(self, item: "deque[Any]") -> None:
        super().__init__()
        self._value: deque[Any] = item

    def __setitem__(self, key: Any, item: "deque[Any]") -> None:
        self._value = item

    def __getitem__(self, key: Any) -> "deque[Any]":
        return self._value

    def __repr__(self) -> str:
        return repr(f"{{'*': {self._value}}}")
