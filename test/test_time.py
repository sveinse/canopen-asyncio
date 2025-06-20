import asyncio
import struct
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import canopen_asyncio
import canopen_asyncio as canopen
import canopen_asyncio.timestamp


class TestTime(unittest.IsolatedAsyncioTestCase):

    __test__ = False  # This is a base class, tests should not be run directly.
    use_async: bool

    def setUp(self):
        self.loop = None
        if self.use_async:
            self.loop = asyncio.get_event_loop()

    async def test_epoch(self):
        """Verify that the epoch matches the standard definition."""
        epoch = datetime.strptime(
            "1984-01-01 00:00:00 +0000", "%Y-%m-%d %H:%M:%S %z"
        ).timestamp()
        self.assertEqual(int(epoch), canopen.timestamp.OFFSET)

    async def test_time_producer(self):
        network = canopen.Network(loop=self.loop)
        self.addCleanup(network.disconnect)
        network.NOTIFIER_SHUTDOWN_TIMEOUT = 0.0
        network.connect(interface="virtual", receive_own_messages=True)
        producer = canopen.timestamp.TimeProducer(network)

        # Provide a specific time to verify the proper encoding
        producer.transmit(1_927_999_438)  # 2031-02-04T19:23:58+00:00
        msg = network.bus.recv(1)
        self.assertEqual(msg.arbitration_id, 0x100)
        self.assertEqual(msg.dlc, 6)
        self.assertEqual(msg.data, b"\xb0\xa4\x29\x04\x31\x43")

        # Test again with the current time as implicit timestamp
        current = time.time()
        with patch("canopen_asyncio.timestamp.time.time", return_value=current):
            current_from_epoch = current - canopen_asyncio.timestamp.OFFSET
            producer.transmit()
            msg = network.bus.recv(1)
            self.assertEqual(msg.arbitration_id, 0x100)
            self.assertEqual(msg.dlc, 6)
            ms, days = struct.unpack("<LH", msg.data)
            self.assertEqual(days, int(current_from_epoch) // 86400)
            self.assertEqual(ms, int(current_from_epoch % 86400 * 1000))


class TestTimeSync(TestTime):
    """ Test time functions in synchronous mode. """
    __test__ = True
    use_async = False


class TestTimeAsync(TestTime):
    """ Test time functions in asynchronous mode. """
    __test__ = True
    use_async = True


if __name__ == "__main__":
    unittest.main()
