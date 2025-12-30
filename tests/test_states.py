from time import sleep
from unittest import TestCase, skipIf

from tfism import Machine, MachineError
from tfism.extensions import MachineFactory
from tfism.extensions.states import *

from .test_core import TYPE_CHECKING
from .test_graphviz import TestDiagramsLockedNested, pgv

try:
    from unittest.mock import MagicMock
except ImportError:
    from unittest.mock import MagicMock


if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Type

    from tfism.core import TransitionConfig


class TestTransitions(TestCase):
    def setUp(self):
        super().setUp()
        self.machine_cls = Machine  # type: Type[Machine]

    def test_tags(self):

        if TYPE_CHECKING:

            @add_state_features(Tags)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Tags)
            class CustomMachine(self.machine_cls):
                pass

        states = [{"name": "A", "tags": ["initial", "success", "error_state"]}]
        m = CustomMachine(states=states, initial="A")
        s = m.get_state(m.state)
        self.assertTrue(s.is_initial)
        self.assertTrue(s.is_success)
        self.assertTrue(s.is_error_state)
        self.assertFalse(s.is_not_available)

    def test_error(self):

        if TYPE_CHECKING:

            @add_state_features(Error)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Error)
            class CustomMachine(self.machine_cls):
                pass

        states = ["A", "B", "F", {"name": "S1", "tags": ["accepted"]}, {"name": "S2", "accepted": True}]

        transitions = [["to_B", ["S1", "S2"], "B"], ["go", "A", "B"], ["fail", "B", "F"], ["success1", "B", "S2"], ["success2", "B", "S2"]]  # type: Sequence[TransitionConfig]
        m = CustomMachine(states=states, transitions=transitions, auto_transitions=False, initial="A")
        m.go()
        m.success1()
        self.assertTrue(m.get_state(m.state).is_accepted)
        m.to_B()
        m.success2()
        self.assertTrue(m.get_state(m.state).is_accepted)
        m.to_B()
        with self.assertRaises(MachineError):
            m.fail()

    def test_error_callback(self):

        if TYPE_CHECKING:

            @add_state_features(Error)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Error)
            class CustomMachine(self.machine_cls):
                pass

        mock_callback = MagicMock()

        states = ["A", {"name": "B", "on_enter": mock_callback}, "C"]
        transitions = [
            ["to_B", "A", "B"],
            ["to_C", "B", "C"],
        ]
        m = CustomMachine(states=states, transitions=transitions, auto_transitions=False, initial="A")
        m.to_B()
        self.assertEqual(m.state, "B")
        self.assertTrue(mock_callback.called)

    def test_timeout(self):
        mock = MagicMock()

        if TYPE_CHECKING:

            @add_state_features(Timeout)
            class CustomMachine(Machine):
                def timeout(self):
                    mock()

        else:

            @add_state_features(Timeout)
            class CustomMachine(self.machine_cls):
                def timeout(self):
                    mock()

        states = ["A", {"name": "B", "timeout": 0.3, "on_timeout": "timeout"}, {"name": "C", "timeout": 0.3, "on_timeout": mock}]

        m = CustomMachine(states=states)
        m.to_B()
        m.to_A()
        sleep(0.4)
        self.assertFalse(mock.called)
        m.to_B()
        sleep(0.4)
        self.assertTrue(mock.called)
        m.to_C()
        sleep(0.4)
        self.assertEqual(mock.call_count, 2)

        with self.assertRaises(AttributeError):
            m.add_state({"name": "D", "timeout": 0.3})

    def test_timeout_callbacks(self):
        timeout = MagicMock()
        notification = MagicMock()
        counter = MagicMock()

        if TYPE_CHECKING:

            @add_state_features(Timeout)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Timeout)
            class CustomMachine(self.machine_cls):
                pass

        class Model:
            def on_timeout_B(self):
                counter()

            def timeout(self):
                timeout()

            def notification(self):
                notification()

            def another_notification(self):
                notification()

        states = ["A", {"name": "B", "timeout": 0.05, "on_timeout": "timeout"}]
        model = Model()
        machine = CustomMachine(model=model, states=states, initial="A")
        model.to_B()
        sleep(0.1)
        self.assertTrue(timeout.called)
        self.assertTrue(counter.called)
        machine.get_state("B").add_callback("on_timeout", "notification")
        machine.on_timeout_B("another_notification")
        model.to_B()
        sleep(0.1)
        self.assertEqual(timeout.call_count, 2)
        self.assertEqual(counter.call_count, 2)
        self.assertTrue(notification.called)
        machine.get_state("B").on_timeout = []
        model.to_B()
        sleep(0.1)
        self.assertEqual(timeout.call_count, 2)
        self.assertEqual(notification.call_count, 2)

    def test_timeout_transitioning(self):
        timeout_mock = MagicMock()

        if TYPE_CHECKING:

            @add_state_features(Timeout)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Timeout)
            class CustomMachine(self.machine_cls):
                pass

        states = ["A", {"name": "B", "timeout": 0.05, "on_timeout": ["to_A", timeout_mock]}]
        machine = CustomMachine(states=states, initial="A")
        machine.to_B()
        sleep(0.1)
        self.assertTrue(machine.is_A())
        self.assertTrue(timeout_mock.called)

    def test_volatile(self):

        class TemporalState:
            def __init__(self):
                self.value = 5

            def increase(self):
                self.value += 1

        if TYPE_CHECKING:

            @add_state_features(Volatile)
            class CustomMachine(Machine):
                pass

        else:

            @add_state_features(Volatile)
            class CustomMachine(self.machine_cls):
                pass

        states = ["A", {"name": "B", "volatile": TemporalState}]
        m = CustomMachine(states=states, initial="A")

        m.to_B()
        self.assertEqual(m.scope.value, 5)

        # should call method of TemporalState
        m.scope.increase()
        self.assertEqual(m.scope.value, 6)

        # re-entering state should reset default volatile object
        m.to_A()
        self.assertFalse(hasattr(m.scope, "value"))

        m.scope.foo = "bar"
        m.to_B()
        # custom attribute of A should be gone
        self.assertFalse(hasattr(m.scope, "foo"))
        # value should be reset
        self.assertEqual(m.scope.value, 5)


@skipIf(pgv is None, "Graph diagram requires pygraphviz")
class TestStatesDiagramsLockedNested(TestDiagramsLockedNested):
    def setUp(self):

        machine_cls = MachineFactory.get_predefined(locked=True, nested=True, graph=True)

        @add_state_features(Error, Timeout, Volatile)
        class CustomMachine(machine_cls):  # type: ignore
            pass

        super().setUp()
        self.machine_cls = CustomMachine

    def test_nested_notebook(self):
        # test will create a custom state machine already. This will cause errors when inherited.
        self.assertTrue(True)


class TestCustomDynamicMethods(TestCase):
    """Test cases for custom dynamic_methods that don't start with 'on_'."""

    def test_custom_dynamic_method_without_on_prefix(self):
        """Test that custom dynamic_methods without 'on_' prefix work correctly.

        After the fix, add_callback should accept the full method name from dynamic_methods,
        not requiring callers to strip a prefix with [3:].
        """

        from tfism.core import State

        # Custom State with a dynamic_method that doesn't start with 'on_'
        class CustomState(State):
            """A custom state with a 'before_enter' callback list."""

            dynamic_methods = ["before_enter"]

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.before_enter = []

        # Test that we can create a state with the custom method
        state = CustomState("test_state")

        def callback_func():
            pass

        # After the fix, we should be able to pass the full method name
        state.add_callback("before_enter", callback_func)
        self.assertEqual(len(state.before_enter), 1)

        # Verify that the old [3:] pattern is no longer needed and would be wrong
        # For 'before_enter', [3:] would give 'ore_enter' which doesn't exist
        method_name = "before_enter"
        # This would be the old (incorrect) way:
        # trigger = method_name[3:]  # 'ore_enter' - WRONG!
        # The new (correct) way is to use the full method name:
        state.add_callback(method_name, callback_func)
        self.assertEqual(len(state.before_enter), 2)

    def test_standard_on_prefix_methods_still_work(self):
        """Test that standard dynamic_methods with 'on_' prefix still work correctly."""

        from tfism.core import State

        # Standard State with 'on_' prefix methods
        state = State("test")

        def callback_func():
            pass

        # After the fix, we pass the full method name including 'on_' prefix
        state.add_callback("on_enter", callback_func)
        self.assertEqual(len(state.on_enter), 1)

        state.add_callback("on_exit", callback_func)
        self.assertEqual(len(state.on_exit), 1)

    def test_machine_integration_with_custom_dynamic_methods(self):
        """Test that custom dynamic_methods work correctly with Machine integration."""

        from tfism.core import State, Machine

        # Custom State with a custom callback method
        class CustomState(State):
            dynamic_methods = ["before_enter", "after_exit"]

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.before_enter = []
                self.after_exit = []

        # Custom Machine that uses the CustomState
        class CustomMachine(Machine):
            state_cls = CustomState

        # Track callback execution
        callback_called = []

        def on_before_enter_A():
            callback_called.append("before_enter_A")

        def on_after_exit_A():
            callback_called.append("after_exit_A")

        # Create model and machine
        class Model:
            pass

        model = Model()
        model.on_before_enter_A = on_before_enter_A
        model.on_after_exit_A = on_after_exit_A

        machine = CustomMachine(model=model, states=["A", "B"], initial="A")

        # Manually add callbacks using the new interface
        state_a = machine.get_state("A")
        state_a.add_callback("before_enter", "on_before_enter_A")
        state_a.add_callback("after_exit", "on_after_exit_A")

        # The callbacks should be in the lists
        self.assertEqual(len(state_a.before_enter), 1)
        self.assertEqual(len(state_a.after_exit), 1)
