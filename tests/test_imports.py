def test_imports() -> None:
    from tfism import Machine
    from tfism.extensions import (
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
        from tfism.extensions import AsyncGraphMachine, AsyncMachine, HierarchicalAsyncGraphMachine, HierarchicalAsyncMachine
    except (ImportError, SyntaxError):  # pragma: no cover
        pass
