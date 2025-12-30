"""Test AsyncMachine.pocket functionality"""
import asyncio
import unittest

from tfism.extensions.asyncio import AsyncMachine


class TestAsyncMachinePocket(unittest.TestCase):
    def setUp(self):
        self.machine_cls = AsyncMachine

    def test_pocket_basic_set_get_async(self):
        """Test basic pocket set and get operations with AsyncMachine"""
        async def run_test():
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

        asyncio.run(run_test())

    def test_pocket_cleared_on_exit_async(self):
        """Test that pocket is cleared when exiting a state with AsyncMachine"""

        class Model:
            async def on_aenter_B(self, event_data):
                event_data.state.pocket = "Data in B"

        async def run_test():
            model = Model()
            m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=True, auto_transitions=True)

            # Set pocket in state B
            state_b = m.get_state("B")
            await model.to_B()
            self.assertEqual(state_b.pocket, "Data in B")

            # Transition to A
            await model.to_A()

            # Pocket in B should be cleared
            self.assertIsNone(state_b.pocket)

        asyncio.run(run_test())

    def test_pocket_in_on_enter_callback_async(self):
        """Test setting pocket in on_enter callback with AsyncMachine"""

        class Model:
            async def on_aenter_B(self, event_data):
                event_data.state.pocket = {"status": "success", "value": 42}

        async def run_test():
            model = Model()
            m = self.machine_cls(model=model, states=["A", "B"], initial="A", send_event=True, auto_transitions=True)

            # Transition to B
            await model.to_B()

            # Check pocket was set
            state_b = m.get_state("B")
            self.assertEqual(state_b.pocket, {"status": "success", "value": 42})

        asyncio.run(run_test())

    def test_pocket_with_async_callbacks(self):
        """Test pocket with async callbacks"""

        class Model:
            def __init__(self):
                self.processing_started = False
                self.result = None

            async def on_aenter_processing(self, event_data):
                self.processing_started = True
                await asyncio.sleep(0.01)  # Simulate async work
                event_data.state.pocket = {"processed": True, "value": 123}

            async def on_aexit_processing(self, event_data):
                # Pocket should still be available
                self.result = event_data.state.pocket
                await asyncio.sleep(0.01)  # Simulate cleanup

        async def run_test():
            model = Model()
            m = self.machine_cls(
                model=model, states=["idle", "processing"], initial="idle", send_event=True, auto_transitions=True
            )

            # Start processing
            await model.to_processing()
            self.assertTrue(model.processing_started)

            # Check pocket
            processing_state = m.get_state("processing")
            self.assertEqual(processing_state.pocket, {"processed": True, "value": 123})

            # Exit processing
            await model.to_idle()

            # Check that on_exit could access pocket
            self.assertEqual(model.result, {"processed": True, "value": 123})

            # Pocket should be cleared
            self.assertIsNone(processing_state.pocket)

        asyncio.run(run_test())

    def test_pocket_with_queued_transitions_async(self):
        """Test pocket behavior with queued transitions in AsyncMachine"""

        class Model:
            def __init__(self):
                self.results = []

            async def on_aenter_B(self, event_data):
                event_data.state.pocket = "B pocket"
                self.results.append(("enter_B", event_data.state.pocket))
                # Trigger another transition
                await event_data.model.to_C()

            async def on_aenter_C(self, event_data):
                event_data.state.pocket = "C pocket"
                self.results.append(("enter_C", event_data.state.pocket))

        async def run_test():
            model = Model()
            m = self.machine_cls(
                model=model, states=["A", "B", "C"], initial="A", send_event=True, queued=True, auto_transitions=True
            )

            # Transition from A to B (which will trigger transition to C)
            await model.to_B()

            # Check both pockets
            state_b = m.get_state("B")
            state_c = m.get_state("C")

            # After all transitions complete
            self.assertEqual(model.state, "C")
            # B should have exited
            self.assertIsNone(state_b.pocket)
            # C should still be active
            self.assertEqual(state_c.pocket, "C pocket")

        asyncio.run(run_test())

    def test_pocket_with_multiple_state_transitions_async(self):
        """Test pocket behavior across multiple state transitions with AsyncMachine"""

        class Model:
            async def on_aenter_B(self, event_data):
                event_data.state.pocket = "B data"

            async def on_aenter_C(self, event_data):
                event_data.state.pocket = "C data"

        async def run_test():
            model = Model()
            m = self.machine_cls(
                model=model, states=["A", "B", "C"], initial="A", send_event=True, auto_transitions=True
            )

            # A -> B
            await model.to_B()
            state_b = m.get_state("B")
            self.assertEqual(state_b.pocket, "B data")

            # B -> C
            await model.to_C()
            state_c = m.get_state("C")
            self.assertEqual(state_c.pocket, "C data")
            # B's pocket should be cleared
            self.assertIsNone(state_b.pocket)

        asyncio.run(run_test())

    def test_pocket_in_async_on_exit_callback(self):
        """Test that pocket is still available in async on_exit callback"""

        class Model:
            def __init__(self):
                self.pocket_value_in_exit = None

            async def on_aenter_B(self, event_data):
                event_data.state.pocket = "Will be checked in exit"

            async def on_aexit_B(self, event_data):
                # Pocket should still be available here
                self.pocket_value_in_exit = event_data.state.pocket
                await asyncio.sleep(0.01)  # Simulate async work

        async def run_test():
            model = Model()
            m = self.machine_cls(
                model=model, states=["A", "B", "C"], initial="A", send_event=True, auto_transitions=True
            )

            # A -> B
            await model.to_B()

            # B -> C (triggers on_exit_B)
            await model.to_C()

            # Check that on_exit could access the pocket
            self.assertEqual(model.pocket_value_in_exit, "Will be checked in exit")

            # Pocket should be cleared after on_exit
            state_b = m.get_state("B")
            self.assertIsNone(state_b.pocket)

        asyncio.run(run_test())

    def test_pocket_with_async_exception_handling(self):
        """Test pocket with async exception handling"""

        class Model:
            def __init__(self):
                self.exception_caught = False

            async def on_enter_B(self, event_data):
                event_data.state.pocket = {"before_exception": True}

            async def on_prepare_error(self, event_data):
                # This will raise an error
                raise ValueError("Test exception")

            async def on_exception(self, event_data):
                self.exception_caught = True
                # Pocket should still be accessible even during exception handling
                if hasattr(event_data, 'state') and event_data.state:
                    event_data.state.pocket = {"exception_handled": True}

        async def run_test():
            model = Model()
            m = self.machine_cls(
                model=model,
                states=["A", "B"],
                initial="A",
                send_event=True,
                auto_transitions=True,
                on_exception="on_exception",
            )

            # Add transition that will fail
            m.add_transition("fail", "A", "B", prepare="on_prepare_error")

            # Trigger the failing transition
            try:
                await model.fail()
            except ValueError:
                pass  # Expected

            # Check exception was caught
            self.assertTrue(model.exception_caught)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
