import unittest

from canopen_asyncio.utils import pretty_index


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


if __name__ == "__main__":
    unittest.main()
