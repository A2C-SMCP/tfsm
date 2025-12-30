"""Test State.pocket functionality"""

import unittest

from tfsm.core import Machine


class TestStatePocket(unittest.TestCase):
    def setUp(self):
        self.machine_cls = Machine

    def test_pocket_basic_set_get(self):
        """Test basic pocket set and get operations"""
        m = self.machine_cls(states=["A", "B"], initial="A")
        state_a = m.get_state("A")

        # Pocket should be None initially
        self.assertIsNone(state_a.pocket)

        # Set and retrieve a simple value
        state_a.pocket = "Hello"
        self.assertEqual(state_a.pocket, "Hello")

        # Set and retrieve a complex value
        state_a.pocket = {"key": "value", "count": 42}
        self.assertEqual(state_a.pocket, {"key": "value", "count": 42})

        # Set and retrieve a list
        state_a.pocket = [1, 2, 3]
        self.assertEqual(state_a.pocket, [1, 2, 3])

    def test_pocket_cleared_on_exit(self):
        """Test that pocket is cleared when exiting a state"""
        m = self.machine_cls(states=["A", "B"], initial="A")

        # Set pocket in state A
        state_a = m.get_state("A")
        state_a.pocket = "Data in A"
        self.assertEqual(state_a.pocket, "Data in A")

        # Transition to B
        m.to_B()

        # Pocket in A should be cleared
        self.assertIsNone(state_a.pocket)

    def test_pocket_in_on_enter_callback_with_send_event(self):
        """Test setting pocket in on_enter callback with send_event=True"""

        class Model:
            def __init__(self):
                self.captured_value = None

            def on_enter_B(self, event_data):
                event_data.state.pocket = {"status": "success", "value": 42}

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=True, auto_transitions=True)

        # Transition to B (trigger is on model, not machine)
        model.to_B()

        # Check pocket was set
        state_b = m.get_state("B")
        self.assertEqual(state_b.pocket, {"status": "success", "value": 42})

    def test_pocket_in_on_enter_callback_without_send_event(self):
        """Test that pocket is accessible without send_event"""

        class Model:
            def __init__(self):
                self.machine = None

            def on_enter_B(self):
                # Without send_event, we need to access state differently
                # This test verifies pocket can still be used
                state = self.machine.get_state("B")
                state.pocket = "No event data"

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=False, auto_transitions=True)
        model.machine = m

        # Transition to B
        model.to_B()

        # Check pocket was set
        state_b = m.get_state("B")
        self.assertEqual(state_b.pocket, "No event data")

    def test_pocket_with_multiple_state_transitions(self):
        """Test pocket behavior across multiple state transitions"""

        class Model:
            def on_enter_B(self, event_data):
                event_data.state.pocket = "B data"

            def on_enter_C(self, event_data):
                event_data.state.pocket = "C data"

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B", "C"], initial="A", send_event=True, auto_transitions=True)

        # A -> B
        model.to_B()
        state_b = m.get_state("B")
        self.assertEqual(state_b.pocket, "B data")

        # B -> C
        model.to_C()
        state_c = m.get_state("C")
        self.assertEqual(state_c.pocket, "C data")
        # B's pocket should be cleared
        self.assertIsNone(state_b.pocket)

    def test_pocket_in_on_exit_callback(self):
        """Test that pocket is still available in on_exit callback"""

        class Model:
            def __init__(self):
                self.pocket_value_in_exit = None

            def on_enter_B(self, event_data):
                event_data.state.pocket = "Will be checked in exit"

            def on_exit_B(self, event_data):
                # Pocket should still be available here
                self.pocket_value_in_exit = event_data.state.pocket

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B", "C"], initial="A", send_event=True, auto_transitions=True)

        # A -> B
        model.to_B()

        # B -> C (triggers on_exit_B)
        model.to_C()

        # Check that on_exit could access the pocket
        self.assertEqual(model.pocket_value_in_exit, "Will be checked in exit")

        # Pocket should be cleared after on_exit
        state_b = m.get_state("B")
        self.assertIsNone(state_b.pocket)

    def test_pocket_with_auto_transitions(self):
        """Test pocket with auto-generated to_X transitions"""

        class Model:
            def on_enter_B(self, event_data):
                event_data.state.pocket = "Auto transition data"

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B", "C"], initial="A", send_event=True, auto_transitions=True)

        # Use auto-generated transition
        model.to_B()

        state_b = m.get_state("B")
        self.assertEqual(state_b.pocket, "Auto transition data")

    def test_pocket_with_overwrite(self):
        """Test overwriting pocket value multiple times"""

        class Model:
            def on_enter_B(self, event_data):
                event_data.state.pocket = "First"
                event_data.state.pocket = "Second"
                event_data.state.pocket = "Third"

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=True, auto_transitions=True)

        model.to_B()

        state_b = m.get_state("B")
        self.assertEqual(state_b.pocket, "Third")

    def test_pocket_with_none_value(self):
        """Test setting pocket to None explicitly"""

        m = self.machine_cls(states=["A"], initial="A")
        state_a = m.get_state("A")

        # Set a value
        state_a.pocket = "Data"
        self.assertEqual(state_a.pocket, "Data")

        # Explicitly set to None
        state_a.pocket = None
        self.assertIsNone(state_a.pocket)

    def test_pocket_with_complex_objects(self):
        """Test pocket with complex Python objects"""

        class CustomClass:
            def __init__(self, value):
                self.value = value

        m = self.machine_cls(states=["A"], initial="A")
        state_a = m.get_state("A")

        # Set custom object
        obj = CustomClass(42)
        state_a.pocket = obj
        self.assertIs(state_a.pocket, obj)
        self.assertEqual(state_a.pocket.value, 42)

        # Set nested structure
        state_a.pocket = {"nested": {"list": [1, 2, 3], "obj": obj}}
        self.assertEqual(state_a.pocket["nested"]["obj"].value, 42)

    def test_pocket_with_queued_transitions(self):
        """Test pocket behavior with queued transitions"""

        class Model:
            def __init__(self):
                self.results = []

            def on_enter_B(self, event_data):
                event_data.state.pocket = "B pocket"
                self.results.append(("enter_B", event_data.state.pocket))
                # Trigger another transition while in callback
                # Use the model's trigger, not the machine's
                event_data.model.to_C()

            def on_enter_C(self, event_data):
                event_data.state.pocket = "C pocket"
                self.results.append(("enter_C", event_data.state.pocket))

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B", "C"], initial="A", send_event=True, queued=True, auto_transitions=True)

        # Transition from A to B (which will queue transition to C)
        model.to_B()

        # Check both pockets were set correctly
        state_b = m.get_state("B")
        state_c = m.get_state("C")

        # After all transitions complete, check states (use model to get state)
        self.assertEqual(model.state, "C")
        # In queued mode, after all transitions, B should have exited
        self.assertIsNone(state_b.pocket)
        # C should still be active
        self.assertEqual(state_c.pocket, "C pocket")

    def test_pocket_with_internal_transitions(self):
        """Test pocket with internal transitions (dest=None)"""

        class Model:
            def on_prepare_internal(self, event_data):
                # Get current state from model
                current_state_name = getattr(event_data.model, event_data.machine.model_attribute)
                current_state = event_data.machine.get_state(current_state_name)
                current_state.pocket = "Internal transition data"

        model = Model()
        m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=True, auto_transitions=True)

        # Add internal transition (no state change)
        m.add_transition("internal", "A", None, prepare="on_prepare_internal")

        # Trigger internal transition
        model.internal()

        # Pocket should be set since state didn't change (no exit called)
        state_a = m.get_state("A")
        self.assertEqual(state_a.pocket, "Internal transition data")

        # Now transition to B
        model.to_B()

        # Pocket should be cleared
        self.assertIsNone(state_a.pocket)


if __name__ == "__main__":
    unittest.main()
