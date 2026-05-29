"""Tests for the ``callback_scope`` feature.

``callback_scope`` decouples the object that *provides callbacks* (a long-lived handler) from the
object that *holds state and trigger methods* (the ``model``). This is the foundation of the
"per-run machine" pattern: a single long-lived handler can drive arbitrarily many concurrent runs,
each with its own per-run context object as the model, while business callbacks resolve against the
shared handler.

See ``Machine.__init__`` / ``Machine.add_model`` for the parameter semantics.
"""

import asyncio
from unittest import TestCase, skipIf

from tfism import Machine
from tfism.extensions.nesting import HierarchicalMachine

try:
    from tfism.extensions.asyncio import AsyncMachine
except (ImportError, SyntaxError):
    AsyncMachine = None  # type: ignore


STATES = ["init", "thinking", "acting"]
TRANSITIONS = [
    {"trigger": "think", "source": "init", "dest": "thinking",
     "before": "before_think", "conditions": "may_think"},
    {"trigger": "act", "source": "thinking", "dest": "acting", "after": "after_act"},
]

# For AsyncMachine the auto-weave convention is ``on_aenter_<state>`` (AsyncState.dynamic_methods),
# so ``on_enter_thinking`` would NOT be auto-woven. The robust, recommended pattern is to declare the
# enter/exit callbacks explicitly by name in the state config; they are resolved against the scope.
ASYNC_STATES = [
    "init",
    {"name": "thinking", "on_enter": "on_enter_thinking", "on_exit": "on_exit_thinking"},
    "acting",
]


class Handler:
    """Long-lived business object. Provides callbacks by *string name* (idiomatic tfism)."""

    def __init__(self):
        self.events = []

    def before_think(self, event):
        self.events.append(("before_think", id(event.model)))

    def may_think(self, event):
        return True

    def after_act(self, event):
        self.events.append(("after_act", id(event.model)))

    def on_enter_thinking(self, event):
        event.state.pocket = {"owner": id(event.model)}
        self.events.append(("enter_thinking", id(event.model)))

    def on_exit_thinking(self, event):
        self.events.append(("exit_thinking", id(event.model)))


class SubHandler(Handler):
    def before_think(self, event):
        self.events.append(("overridden_before_think", id(event.model)))


class Ctx:
    """Per-run context object used as the FSM model (holds ``state`` and trigger methods)."""


class TestCallbackScope(TestCase):
    def test_string_callbacks_resolve_on_scope(self):
        handler = Handler()
        ctx = Ctx()
        Machine(model=ctx, callback_scope=handler, states=STATES, transitions=TRANSITIONS,
                initial="init", auto_transitions=False, send_event=True)

        result = ctx.think()

        self.assertTrue(result)
        self.assertEqual(ctx.state, "thinking")
        # The string callbacks 'before_think' and 'may_think' were resolved on the handler...
        self.assertIn(("before_think", id(ctx)), handler.events)
        # ...and event.model inside the callback is the ctx, not the handler.
        self.assertEqual(handler.events[0][1], id(ctx))

    def test_state_and_triggers_live_on_model_not_scope(self):
        handler = Handler()
        ctx = Ctx()
        Machine(model=ctx, callback_scope=handler, states=STATES, transitions=TRANSITIONS,
                initial="init", auto_transitions=False, send_event=True)

        # State and trigger convenience methods are stamped onto the model (ctx)...
        self.assertEqual(ctx.state, "init")
        self.assertTrue(hasattr(ctx, "think"))
        self.assertTrue(hasattr(ctx, "is_init"))
        # ...and NOT onto the callback scope (handler).
        self.assertFalse(hasattr(handler, "state"))
        self.assertFalse(hasattr(handler, "think"))

    def test_on_enter_exit_auto_woven_from_scope(self):
        handler = Handler()
        ctx = Ctx()
        Machine(model=ctx, callback_scope=handler, states=STATES, transitions=TRANSITIONS,
                initial="init", auto_transitions=False, send_event=True)

        ctx.think()  # enters 'thinking' -> on_enter_thinking auto-woven from handler
        self.assertIn(("enter_thinking", id(ctx)), handler.events)
        ctx.act()    # exits 'thinking' -> on_exit_thinking auto-woven from handler
        self.assertIn(("exit_thinking", id(ctx)), handler.events)
        self.assertIn(("after_act", id(ctx)), handler.events)

    def test_subclass_override_is_honored(self):
        handler = SubHandler()
        ctx = Ctx()
        Machine(model=ctx, callback_scope=handler, states=STATES, transitions=TRANSITIONS,
                initial="init", auto_transitions=False, send_event=True)

        ctx.think()
        # The subclass override of a *string-named* callback wins (resolved via live getattr).
        self.assertIn(("overridden_before_think", id(ctx)), handler.events)
        self.assertNotIn(("before_think", id(ctx)), handler.events)

    def test_callback_scope_none_is_legacy_behavior(self):
        # Without a callback_scope, callbacks resolve against the model itself (unchanged behavior).
        handler = Handler()  # used directly as the model
        Machine(model=handler, states=STATES, transitions=TRANSITIONS,
                initial="init", auto_transitions=False, send_event=True)

        handler.think()
        self.assertEqual(handler.state, "thinking")
        self.assertIn(("before_think", id(handler)), handler.events)
        self.assertIn(("enter_thinking", id(handler)), handler.events)

    def test_per_model_scope_override(self):
        # A single machine, two models, each with its own callback scope.
        machine = Machine(model=None, states=STATES, transitions=TRANSITIONS,
                          initial="init", auto_transitions=False, send_event=True)
        handler_a = Handler()
        handler_b = Handler()
        ctx_a = Ctx()
        ctx_b = Ctx()
        machine.add_model(ctx_a, callback_scope=handler_a)
        machine.add_model(ctx_b, callback_scope=handler_b)

        ctx_a.think()
        ctx_b.think()

        self.assertIn(("before_think", id(ctx_a)), handler_a.events)
        self.assertEqual(handler_b.events, [("before_think", id(ctx_b)),
                                            ("enter_thinking", id(ctx_b))])
        # No crosstalk: handler_a never saw ctx_b and vice versa.
        self.assertNotIn(("before_think", id(ctx_b)), handler_a.events)

    def test_remove_model_clears_scope(self):
        machine = Machine(model=None, states=STATES, transitions=TRANSITIONS,
                          initial="init", auto_transitions=False, send_event=True)
        handler = Handler()
        ctx = Ctx()
        machine.add_model(ctx, callback_scope=handler)
        self.assertIn(id(ctx), machine._callback_scopes)

        machine.remove_model(ctx)
        self.assertNotIn(id(ctx), machine._callback_scopes)

    def test_machine_default_scope_applies_to_all_models(self):
        handler = Handler()
        ctx1 = Ctx()
        ctx2 = Ctx()
        machine = Machine(model=ctx1, callback_scope=handler, states=STATES,
                          transitions=TRANSITIONS, initial="init",
                          auto_transitions=False, send_event=True)
        machine.add_model(ctx2)  # no explicit scope -> inherits the machine default

        ctx1.think()
        ctx2.think()
        self.assertIn(("before_think", id(ctx1)), handler.events)
        self.assertIn(("before_think", id(ctx2)), handler.events)


class TestCallbackScopeHierarchical(TestCase):
    def test_nested_on_enter_resolves_on_scope(self):
        handler = Handler()
        ctx = Ctx()
        states = ["init", {"name": "thinking", "children": ["sub"]}]
        transitions = [
            {"trigger": "think", "source": "init", "dest": "thinking",
             "before": "before_think"},
        ]
        HierarchicalMachine(model=ctx, callback_scope=handler, states=states,
                            transitions=transitions, initial="init",
                            auto_transitions=False, send_event=True)

        ctx.think()
        self.assertIn(("before_think", id(ctx)), handler.events)
        self.assertIn(("enter_thinking", id(ctx)), handler.events)


@skipIf(AsyncMachine is None, "AsyncMachine requires asyncio support")
class TestCallbackScopeAsync(TestCase):
    def test_async_string_callbacks_resolve_on_scope(self):
        handler = Handler()
        ctx = Ctx()
        AsyncMachine(model=ctx, callback_scope=handler, states=ASYNC_STATES,
                     transitions=TRANSITIONS, initial="init",
                     auto_transitions=False, send_event=True)

        asyncio.run(ctx.think())
        self.assertEqual(ctx.state, "thinking")
        self.assertIn(("before_think", id(ctx)), handler.events)
        self.assertIn(("enter_thinking", id(ctx)), handler.events)

    def test_concurrent_runs_share_one_handler_without_crosstalk(self):
        handler = Handler()

        async def run_one():
            ctx = Ctx()
            machine = AsyncMachine(model=ctx, callback_scope=handler, states=ASYNC_STATES,
                                   transitions=TRANSITIONS, initial="init",
                                   auto_transitions=False, send_event=True)
            await ctx.think()
            # Capture the pocket while still in 'thinking' (it is cleared on exit).
            pocket = machine.get_state("thinking").pocket
            await asyncio.sleep(0)  # yield to interleave with sibling runs
            await ctx.act()
            return id(ctx), ctx.state, pocket

        async def main():
            return await asyncio.gather(*[run_one() for _ in range(8)])

        results = asyncio.run(main())

        # Every run reaches the terminal state independently.
        self.assertEqual({state for _, state, _ in results}, {"acting"})
        # Per-run State.pocket is isolated: each run's pocket owner is its own ctx.
        for ctx_id, _, pocket in results:
            self.assertEqual(pocket["owner"], ctx_id)
