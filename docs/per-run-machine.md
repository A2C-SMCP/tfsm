# Per-run machines and `callback_scope`

This guide describes how to drive **many concurrent runs from a single long-lived handler** by giving
each run its own state machine and its own state-holding model, while business callbacks stay on the
shared handler.

## The problem

In tfism a `model` plays three roles at once:

1. **State holder** — the current state lives at `model.<model_attribute>` (default `model.state`).
2. **Trigger surface** — trigger convenience methods (`model.advance()`, `model.is_<state>()`, ...)
   are stamped onto the model.
3. **Callback source** — string callback names (`conditions`, `before`/`after`, `on_enter`/`on_exit`,
   ...) are resolved with `getattr(model, name)`, and `on_enter_<state>` methods on the model are
   auto-woven into the state.

If you build the machine with `Machine(model=self)` on a long-lived object, all per-run state
(`state`, the event queue, `State.pocket`) ends up on that long-lived object. Two concurrent runs then
clobber each other's `state` and pocket — the object cannot be driven concurrently.

## The pattern

Create **one machine per run**, bound to a throw-away per-run context object as the `model`, and point
callback resolution at the long-lived handler with `callback_scope`:

```python
class Handler:                       # long-lived, stateless w.r.t. a run
    def before_think(self, event):   # callbacks are plain (string-named) methods
        ...
    def on_enter_thinking(self, event):
        event.state.pocket = compute(event.model)   # event.model is the per-run ctx

class Ctx:                           # per-run context object (one per run)
    pass

STATES = ["init", "thinking", "acting"]
TRANSITIONS = [
    {"trigger": "think", "source": "init", "dest": "thinking", "before": "before_think"},
    {"trigger": "act", "source": "thinking", "dest": "acting"},
]

def build(handler, ctx):
    return Machine(
        model=ctx,                   # state + triggers land on ctx
        callback_scope=handler,      # string callbacks resolve on handler
        states=STATES,
        transitions=TRANSITIONS,
        initial="init",
        auto_transitions=False,
        send_event=True,
    )
```

Now `ctx.state`, `ctx.think()`, the machine's event queue and the `State.pocket` are all isolated to
that one run, while `before_think` / `on_enter_thinking` execute on the shared `handler`. The same
handler can back any number of concurrent runs without crosstalk.

### Async

The same applies to `AsyncMachine` — each `asyncio.Task` builds its own machine bound to its own `ctx`:

```python
async def run_once(handler):
    ctx = Ctx()
    build_async(handler, ctx)        # AsyncMachine(model=ctx, callback_scope=handler, ...)
    await ctx.think()
    await ctx.act()
```

`AsyncMachine` keeps its transition queue and running-task bookkeeping keyed by `id(model)`, so distinct
per-run `ctx` objects are fully isolated within a single event loop.

## `callback_scope` semantics

- **Default (`None`)**: callbacks resolve against the `model` itself — unchanged, legacy behavior.
- **When set**: string callback names and `on_enter_<state>`/`on_aenter_<state>` auto-weaving resolve
  against `callback_scope`. `state` and trigger convenience methods are *always* stamped onto the
  `model`, never the scope.
- Available as a **keyword-only** argument on the constructor and on
  `add_model(model, callback_scope=...)`. The constructor value is the default scope for every model
  added to that machine; `add_model` can override it per model.

```python
machine = Machine(model=None, states=STATES, transitions=TRANSITIONS, initial="init")
machine.add_model(ctx_a, callback_scope=handler_a)
machine.add_model(ctx_b, callback_scope=handler_b)   # different scope per model
```

## Callables vs string names

Because string names now resolve against the scope, you can keep declaring callbacks by name (the
idiomatic, readable form) even though they live on a different object than the model. Passing already
bound callables (`handler.before_think`) also works and is unaffected by `callback_scope`, since
non-string callbacks are used as-is.

## Auto-weaving note (sync vs async)

`on_enter_<state>` / `on_exit_<state>` auto-weaving uses `state_cls.dynamic_methods`:

- **`Machine`** discovers `on_enter_<state>` / `on_exit_<state>` on the scope.
- **`AsyncMachine`** discovers `on_aenter_<state>` / `on_aexit_<state>` on the scope.

If you prefer a single naming scheme across sync and async, declare the enter/exit callbacks
explicitly in the state config (e.g. `{"name": "thinking", "on_enter": "on_enter_thinking"}`); explicit
string names are resolved against the scope regardless of the auto-weaving convention.
