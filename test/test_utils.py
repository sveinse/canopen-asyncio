import asyncio
import unittest

from canopen.utils import pretty_index, call_callbacks


class TestUtils(unittest.IsolatedAsyncioTestCase):

    def test_pretty_index(self):
        self.assertEqual(pretty_index(0x12ab), "0x12AB")
        self.assertEqual(pretty_index(0x12ab, 0xcd), "0x12AB:CD")
        self.assertEqual(pretty_index(0x12ab, ""), "0x12AB")
        self.assertEqual(pretty_index("test"), "'test'")
        self.assertEqual(pretty_index("test", 0xcd), "'test':CD")
        self.assertEqual(pretty_index(None), "")
        self.assertEqual(pretty_index(""), "")
        self.assertEqual(pretty_index("", ""), "")
        self.assertEqual(pretty_index(None, 0xab), "0xAB")


    def test_call_callbacks_sync(self):

        result1 = 0
        result2 = 0

        def callback1(arg):
            nonlocal result1
            result1 = arg + 1

        def callback2(arg):
            nonlocal result2
            result2 = arg * 2

        # Check that the synchronous callbacks are called correctly
        call_callbacks([callback1, callback2], None, 5)
        self.assertEqual([result1, result2], [6, 10])

        async def async_callback(arg):
            return arg + 1

        # Check that it's not possible to call async callbacks in a non-async context
        with self.assertRaises(RuntimeError):
            call_callbacks([async_callback], None, 5)


    async def test_call_callbacks_async(self):

        result1 = 0
        result2 = 0

        event = asyncio.Event()

        def callback(arg):
            nonlocal result1
            result1 = arg + 1

        async def async_callback(arg):
            nonlocal result2
            result2 = arg * 2
            event.set()  # Notify the test that the async callback is done

        # Check that both callbacks are called correctly in an async context
        loop = asyncio.get_event_loop()
        call_callbacks([callback, async_callback], loop, 5)
        await event.wait()
        self.assertEqual([result1, result2], [6, 10])


if __name__ == "__main__":
    unittest.main()
