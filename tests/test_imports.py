def test_imports() -> None:
    from tfsm import Machine
    from tfsm.extensions import (
        GraphMachine,
        HierarchicalGraphMachine,
        HierarchicalMachine,
        LockedGraphMachine,
        LockedHierarchicalGraphMachine,
        LockedHierarchicalMachine,
        LockedMachine,
        MachineFactory,
    )

    try:
        # only available for Python 3
        from tfsm.extensions import AsyncGraphMachine, AsyncMachine, HierarchicalAsyncGraphMachine, HierarchicalAsyncMachine
    except (ImportError, SyntaxError):  # pragma: no cover
        pass
