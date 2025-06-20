import logging
import threading
import unittest
import asyncio
from contextlib import contextmanager

import can

import canopen_asyncio as canopen
from canopen_asyncio.emcy import EmcyError, EmcyConsumer


TIMEOUT = 0.1


class TestEmcy(unittest.IsolatedAsyncioTestCase):

    __test__ = False  # This is a base class, tests should not be run directly.
    use_async: bool

    def setUp(self):
        loop = None
        if self.use_async:
            loop = asyncio.get_event_loop()
        self.loop = loop

        self.net = canopen.Network(loop=loop)
        self.net.connect(interface="virtual")
        self.emcy = EmcyConsumer()
        self.emcy.network = self.net

    def tearDown(self):
        self.net.disconnect()

    def check_error(self, err, code, reg, data, ts):
        self.assertIsInstance(err, EmcyError)
        self.assertIsInstance(err, Exception)
        self.assertEqual(err.code, code)
        self.assertEqual(err.register, reg)
        self.assertEqual(err.data, data)
        self.assertAlmostEqual(err.timestamp, ts)

    async def dispatch_emcy(self, can_id, data, ts):
        # Dispatch an EMCY datagram.
        if self.use_async:
            await asyncio.to_thread(
                self.emcy.on_emcy, can_id, data, ts
            )
        else:
            self.emcy.on_emcy(can_id, data, ts)

    async def test_emcy_consumer_on_emcy(self):
        # Make sure multiple callbacks receive the same information.
        acc1 = []
        acc2 = []
        self.emcy.add_callback(lambda err: acc1.append(err))
        self.emcy.add_callback(lambda err: acc2.append(err))

        # Dispatch an EMCY datagram.
        await self.dispatch_emcy(0x81, b'\x01\x20\x02\x00\x01\x02\x03\x04', 1000)

        self.assertEqual(len(self.emcy.log), 1)
        self.assertEqual(len(self.emcy.active), 1)

        error = self.emcy.log[0]
        self.assertEqual(self.emcy.active[0], error)
        for err in error, acc1[0], acc2[0]:
            self.check_error(
                error, code=0x2001, reg=0x02,
                data=bytes([0, 1, 2, 3, 4]), ts=1000,
            )

        # Dispatch a new EMCY datagram.
        await self.dispatch_emcy(0x81, b'\x10\x90\x01\x04\x03\x02\x01\x00', 2000)
        self.assertEqual(len(self.emcy.log), 2)
        self.assertEqual(len(self.emcy.active), 2)

        error = self.emcy.log[1]
        self.assertEqual(self.emcy.active[1], error)
        for err in error, acc1[1], acc2[1]:
            self.check_error(
                error, code=0x9010, reg=0x01,
                data=bytes([4, 3, 2, 1, 0]), ts=2000,
            )

        # Dispatch an EMCY reset.
        await self.dispatch_emcy(0x81, b'\x00\x00\x00\x00\x00\x00\x00\x00', 2000)
        self.assertEqual(len(self.emcy.log), 3)
        self.assertEqual(len(self.emcy.active), 0)

    async def test_emcy_consumer_reset(self):
        await self.dispatch_emcy(0x81, b'\x01\x20\x02\x00\x01\x02\x03\x04', 1000)
        await self.dispatch_emcy(0x81, b'\x10\x90\x01\x04\x03\x02\x01\x00', 2000)
        self.assertEqual(len(self.emcy.log), 2)
        self.assertEqual(len(self.emcy.active), 2)

        self.emcy.reset()
        self.assertEqual(len(self.emcy.log), 0)
        self.assertEqual(len(self.emcy.active), 0)

    async def test_emcy_consumer_wait(self):
        if self.use_async:
            self.skipTest("Not implemented for async")

        PAUSE = TIMEOUT / 2

        def push_err():
            self.emcy.on_emcy(0x81, b'\x01\x20\x01\x01\x02\x03\x04\x05', 100)

        def check_err(err):
            self.assertIsNotNone(err)
            self.check_error(
                err, code=0x2001, reg=1,
                data=bytes([1, 2, 3, 4, 5]), ts=100,
            )

        @contextmanager
        def timer(func):
            t = threading.Timer(PAUSE, func)
            try:
                yield t
            finally:
                t.join(TIMEOUT)

        # Check unfiltered wait, on timeout.
        if self.use_async:
            self.assertIsNone(await self.emcy.async_wait(timeout=TIMEOUT))
        else:
            self.assertIsNone(self.emcy.wait(timeout=TIMEOUT))

        # Check unfiltered wait, on success.
        with timer(push_err) as t:
            with self.assertLogs(level=logging.INFO):
                t.start()
                err = self.emcy.wait(timeout=TIMEOUT)
        check_err(err)

        # Check filtered wait, on success.
        with timer(push_err) as t:
            with self.assertLogs(level=logging.INFO):
                t.start()
                err = self.emcy.wait(0x2001, TIMEOUT)
        check_err(err)

        # Check filtered wait, on timeout.
        with timer(push_err) as t:
            t.start()
            self.assertIsNone(self.emcy.wait(0x9000, TIMEOUT))

        def push_reset():
            self.emcy.on_emcy(0x81, b'\x00\x00\x00\x00\x00\x00\x00\x00', 100)

        with timer(push_reset) as t:
            t.start()
            self.assertIsNone(self.emcy.wait(0x9000, TIMEOUT))


class TestEmcySync(TestEmcy):
    """ Run the tests in non-asynchronous mode. """
    __test__ = True
    use_async = False


class TestEmcyAsync(TestEmcy):
    """ Run the tests in asynchronous mode. """
    __test__ = True
    use_async = True


class TestEmcyError(unittest.TestCase):
    def test_emcy_error(self):
        error = EmcyError(0x2001, 0x02, b'\x00\x01\x02\x03\x04', 1000)
        self.assertEqual(error.code, 0x2001)
        self.assertEqual(error.data, b'\x00\x01\x02\x03\x04')
        self.assertEqual(error.register, 2)
        self.assertEqual(error.timestamp, 1000)

    def test_emcy_str(self):
        def check(code, expected):
            err = EmcyError(code, 1, b'', 1000)
            actual = str(err)
            self.assertEqual(actual, expected)

        check(0x2001, "Code 0x2001, Current")
        check(0x3abc, "Code 0x3ABC, Voltage")
        check(0x0234, "Code 0x0234")
        check(0xbeef, "Code 0xBEEF")

    def test_emcy_get_desc(self):
        def check(code, expected):
            err = EmcyError(code, 1, b'', 1000)
            actual = err.get_desc()
            self.assertEqual(actual, expected)

        check(0x0000, "Error Reset / No Error")
        check(0x00ff, "Error Reset / No Error")
        check(0x0100, "")
        check(0x1000, "Generic Error")
        check(0x10ff, "Generic Error")
        check(0x1100, "")
        check(0x2000, "Current")
        check(0x2fff, "Current")
        check(0x3000, "Voltage")
        check(0x3fff, "Voltage")
        check(0x4000, "Temperature")
        check(0x4fff, "Temperature")
        check(0x5000, "Device Hardware")
        check(0x50ff, "Device Hardware")
        check(0x5100, "")
        check(0x6000, "Device Software")
        check(0x6fff, "Device Software")
        check(0x7000, "Additional Modules")
        check(0x70ff, "Additional Modules")
        check(0x7100, "")
        check(0x8000, "Monitoring")
        check(0x8fff, "Monitoring")
        check(0x9000, "External Error")
        check(0x90ff, "External Error")
        check(0x9100, "")
        check(0xf000, "Additional Functions")
        check(0xf0ff, "Additional Functions")
        check(0xf100, "")
        check(0xff00, "Device Specific")
        check(0xffff, "Device Specific")


class TestEmcyProducer(unittest.IsolatedAsyncioTestCase):

    __test__ = False  # This is a base class, tests should not be run directly.
    use_async: bool

    def setUp(self):
        loop = None
        if self.use_async:
            loop = asyncio.get_event_loop()

        self.txbus = can.Bus(interface="virtual", loop=loop)
        self.rxbus = can.Bus(interface="virtual", loop=loop)
        self.net = canopen.Network(self.txbus, loop=loop)
        self.net.NOTIFIER_SHUTDOWN_TIMEOUT = 0.0
        self.net.connect()
        self.emcy = canopen.emcy.EmcyProducer(0x80 + 1)
        self.emcy.network = self.net

    def tearDown(self):
        self.net.disconnect()
        self.txbus.shutdown()
        self.rxbus.shutdown()

    def check_response(self, expected):
        msg = self.rxbus.recv(TIMEOUT)
        self.assertIsNotNone(msg)
        actual = msg.data
        self.assertEqual(actual, expected)

    async def test_emcy_producer_send(self):
        def check(*args, res):
            self.emcy.send(*args)
            self.check_response(res)

        check(0x2001, res=b'\x01\x20\x00\x00\x00\x00\x00\x00')
        check(0x2001, 0x2, res=b'\x01\x20\x02\x00\x00\x00\x00\x00')
        check(0x2001, 0x2, b'\x2a', res=b'\x01\x20\x02\x2a\x00\x00\x00\x00')

    async def test_emcy_producer_reset(self):
        def check(*args, res):
            self.emcy.reset(*args)
            self.check_response(res)

        check(res=b'\x00\x00\x00\x00\x00\x00\x00\x00')
        check(3, res=b'\x00\x00\x03\x00\x00\x00\x00\x00')
        check(3, b"\xaa\xbb", res=b'\x00\x00\x03\xaa\xbb\x00\x00\x00')


class TestEmcyProducerSync(TestEmcyProducer):
    """ Run the tests in non-asynchronous mode. """
    __test__ = True
    use_async = False


class TestEmcyProducerAsync(TestEmcyProducer):
    """ Run the tests in asynchronous mode. """
    __test__ = True
    use_async = True


if __name__ == "__main__":
    unittest.main()
