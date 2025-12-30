# -*- coding: utf-8 -*-
"""Test HierarchicalMachine.pocket functionality"""
import unittest

from tfsm.extensions.nesting import HierarchicalMachine, NestedState


class TestHierarchicalMachinePocket(unittest.TestCase):
    def setUp(self):
        self.states = ["A", "B", {"name": "C", "children": ["1", "2", {"name": "3", "children": ["a", "b", "c"]}]}, "D"]
        self.machine_cls = HierarchicalMachine
        self.state_cls = NestedState

    def test_pocket_in_nested_states(self):
        """Test pocket functionality with nested states"""

        class Model:
            def on_enter_C_3_a(self, event_data):
                event_data.state.pocket = {"nested": "deep", "level": 3}

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Navigate to deeply nested state
        model.to_C()
        model.to_C_3()
        model.to_C_3_a()

        # Check pocket in nested state
        state_c_3_a = m.get_state("C_3_a")
        self.assertEqual(state_c_3_a.pocket, {"nested": "deep", "level": 3})

    def test_pocket_cleared_on_nested_exit(self):
        """Test that pocket is cleared when exiting nested states"""

        class Model:
            def on_enter_C_1(self, event_data):
                event_data.state.pocket = "State C_1 data"

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Enter nested state
        model.to_C()
        model.to_C_1()

        state_c_1 = m.get_state("C_1")
        self.assertEqual(state_c_1.pocket, "State C_1 data")

        # Exit to parent state
        model.to_C_2()

        # Pocket should be cleared
        self.assertIsNone(state_c_1.pocket)

    def test_pocket_with_parallel_nested_states(self):
        """Test pocket with multiple parallel nested states"""

        class Model:
            def on_enter_C_1(self, event_data):
                event_data.state.pocket = "Branch 1"

            def on_enter_C_2(self, event_data):
                event_data.state.pocket = "Branch 2"

            def on_enter_C_3_a(self, event_data):
                event_data.state.pocket = "Deep branch a"

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Test branch 1
        model.to_C()
        model.to_C_1()
        state_c_1 = m.get_state("C_1")
        self.assertEqual(state_c_1.pocket, "Branch 1")

        # Switch to branch 2
        model.to_C_2()
        state_c_2 = m.get_state("C_2")
        self.assertEqual(state_c_2.pocket, "Branch 2")
        # Branch 1's pocket should be cleared
        self.assertIsNone(state_c_1.pocket)

        # Navigate to deep branch
        model.to_C_3()
        model.to_C_3_a()
        state_c_3_a = m.get_state("C_3_a")
        self.assertEqual(state_c_3_a.pocket, "Deep branch a")
        # Branch 2's pocket should be cleared
        self.assertIsNone(state_c_2.pocket)

    def test_pocket_with_auto_transitions_nested(self):
        """Test pocket with auto-generated transitions in nested states"""

        class Model:
            def on_enter_C_3_c(self, event_data):
                event_data.state.pocket = "Auto transition to nested state"

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Use auto-generated transition to deeply nested state
        model.to_C_3_c()

        state_c_3_c = m.get_state("C_3_c")
        self.assertEqual(state_c_3_c.pocket, "Auto transition to nested state")

    def test_pocket_preserved_in_parent_during_child_transition(self):
        """Test that parent state pocket is preserved when transitioning to child"""

        class Model:
            def on_enter_C(self, event_data):
                event_data.state.pocket = "Parent pocket"

            def on_enter_C_3_a(self, event_data):
                # Parent pocket should still be accessible via parent state
                parent_state = event_data.machine.get_state("C")
                event_data.state.pocket = {"child": "a", "parent_pocket": parent_state.pocket}

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Enter parent state
        model.to_C()

        state_c = m.get_state("C")
        self.assertEqual(state_c.pocket, "Parent pocket")

        # Navigate to child
        model.to_C_3()
        model.to_C_3_a()

        # Parent pocket should still be there
        self.assertEqual(state_c.pocket, "Parent pocket")

        # Child should have captured parent's pocket
        state_c_3_a = m.get_state("C_3_a")
        self.assertEqual(state_c_3_a.pocket, {"child": "a", "parent_pocket": "Parent pocket"})

    def test_pocket_with_nested_complex_objects(self):
        """Test pocket with complex objects in nested states"""

        class CustomData:
            def __init__(self, value, nested_state):
                self.value = value
                self.nested_state = nested_state

        class Model:
            def on_enter_C_3_b(self, event_data):
                event_data.state.pocket = CustomData(42, "C_3_b")

        model = Model()
        m = self.machine_cls(model=model, states=self.states, initial="A", send_event=True, auto_transitions=True)

        # Navigate to nested state
        model.to_C()
        model.to_C_3()
        model.to_C_3_b()

        state_c_3_b = m.get_state("C_3_b")
        self.assertIsInstance(state_c_3_b.pocket, CustomData)
        self.assertEqual(state_c_3_b.pocket.value, 42)
        self.assertEqual(state_c_3_b.pocket.nested_state, "C_3_b")
