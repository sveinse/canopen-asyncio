from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import MutableMapping
from typing import Callable, Dict, Final, Iterator, List, Optional, Union

import can
from can import Listener

from canopen_asyncio.async_guard import set_async_sentinel, ensure_not_async
from canopen_asyncio.lss import LssMaster
from canopen_asyncio.nmt import NmtMaster
from canopen_asyncio.node import LocalNode, RemoteNode
from canopen_asyncio.objectdictionary import ObjectDictionary
from canopen_asyncio.objectdictionary.eds import import_from_node
from canopen_asyncio.sync import SyncProducer
from canopen_asyncio.timestamp import TimeProducer


logger = logging.getLogger(__name__)

Callback = Callable[[int, bytearray, float], None]


class Network(MutableMapping):
    """Representation of one CAN bus containing one or more nodes."""

    NOTIFIER_CYCLE: float = 1.0  #: Maximum waiting time for one notifier iteration.
    NOTIFIER_SHUTDOWN_TIMEOUT: float = 5.0  #: Maximum waiting time to stop notifiers.

    # NOTE: Function arguments changed to provide notifier, see #556
    def __init__(self, bus: Optional[can.BusABC] = None, notifier: Optional[can.Notifier] = None,
                 loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        :param can.BusABC bus:
            A python-can bus instance to re-use.
        """
        #: A python-can :class:`can.BusABC` instance which is set after
        #: :meth:`canopen.Network.connect` is called
        self.bus: Optional[can.BusABC] = bus
        self.loop: Optional[asyncio.AbstractEventLoop] = loop
        self._tasks: set[asyncio.Task] = set()
        #: A :class:`~canopen.network.NodeScanner` for detecting nodes
        self.scanner = NodeScanner(self)
        #: List of :class:`can.Listener` objects.
        #: Includes at least MessageListener.
        self.listeners = [MessageListener(self)]
        self.notifier: Optional[can.Notifier] = notifier
        self.nodes: Dict[int, Union[RemoteNode, LocalNode]] = {}
        self.subscribers: Dict[int, List[Callback]] = {}
        self.send_lock = threading.Lock()
        self.sync = SyncProducer(self)
        self.time = TimeProducer(self)
        self.nmt = NmtMaster(0)
        self.nmt.network = self

        self.lss = LssMaster()
        self.lss.network = self

        # Register this function as the means to check if canopen is run in
        # async mode. This enables the @ensure_not_async() decorator to
        # work. See async_guard.py
        set_async_sentinel(self.is_async())

        self.subscribe(self.lss.LSS_RX_COBID, self.lss.on_message_received)

    def subscribe(self, can_id: int, callback: Callback) -> None:
        """Listen for messages with a specific CAN ID.

        :param can_id:
            The CAN ID to listen for.
        :param callback:
            Function to call when message is received.
        """
        self.subscribers.setdefault(can_id, list())
        if callback not in self.subscribers[can_id]:
            self.subscribers[can_id].append(callback)

    def unsubscribe(self, can_id, callback=None) -> None:
        """Stop listening for message.

        :param int can_id:
            The CAN ID from which to unsubscribe.
        :param callback:
            If given, remove only this callback.  Otherwise all callbacks for
            the CAN ID.
        """
        if callback is not None:
            self.subscribers[can_id].remove(callback)
        if not self.subscribers[can_id] or callback is None:
            del self.subscribers[can_id]

    def connect(self, *args, **kwargs) -> Network:
        """Connect to CAN bus using python-can.

        Arguments are passed directly to :class:`can.BusABC`. Typically these
        may include:

        :param channel:
            Backend specific channel for the CAN interface.
        :param str interface:
            Name of the interface. See
            `python-can manual <https://python-can.readthedocs.io/en/stable/configuration.html#interface-names>`__
            for full list of supported interfaces.
        :param int bitrate:
            Bitrate in bit/s.

        :raises can.CanError:
            When connection fails.
        """
        # If bitrate has not been specified, try to find one node where bitrate
        # has been specified
        if "bitrate" not in kwargs:
            for node in self.nodes.values():
                if node.object_dictionary.bitrate:
                    kwargs["bitrate"] = node.object_dictionary.bitrate
                    break
        if self.bus is None:
            self.bus = can.Bus(*args, **kwargs)
        logger.info("Connected to '%s'", self.bus.channel_info)
        if self.notifier is None:
            # Do not start a can notifier with the async loop. It changes the
            # behavior of the notifier callbacks. Instead of running the
            # callbacks from a separate thread, it runs the callbacks in the
            # same thread as the event loop where blocking calls are not allowed.
            # This library needs to support both async and sync, so we need to
            # use the notifier in a separate thread.
            self.notifier = can.Notifier(self.bus, [], self.NOTIFIER_CYCLE)
        for listener in self.listeners:
            self.notifier.add_listener(listener)
        return self

    def disconnect(self) -> None:
        """Disconnect from the CAN bus.

        Must be overridden in a subclass if a custom interface is used.
        """
        for node in self.nodes.values():
            if hasattr(node, "pdo"):
                node.pdo.stop()
        if self.notifier is not None:
            self.notifier.stop(self.NOTIFIER_SHUTDOWN_TIMEOUT)
        if self.bus is not None:
            self.bus.shutdown()
        self.bus = None
        self.check()

        # Remove the async sentinel
        set_async_sentinel(False)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.disconnect()

    async def __aenter__(self):
        # FIXME: When TaskGroup are available, we should use them to manage the
        # tasks. The user must use the `async with` statement with the Network
        # to ensure its created.
        return self

    async def __aexit__(self, type, value, traceback):
        self.disconnect()

    @ensure_not_async  # NOTE: Safeguard for accidental async use
    def add_node(
        self,
        node: Union[int, RemoteNode, LocalNode],
        object_dictionary: Union[str, ObjectDictionary, None] = None,
        upload_eds: bool = False,
    ) -> RemoteNode:
        """Add a remote node to the network.

        :param node:
            Can be either an integer representing the node ID, a
            :class:`canopen.RemoteNode` or :class:`canopen.LocalNode` object.
        :param object_dictionary:
            Can be either a string for specifying the path to an
            Object Dictionary file or a
            :class:`canopen.ObjectDictionary` object.
        :param upload_eds:
            Set ``True`` if EDS file should be uploaded from 0x1021.

        :return:
            The Node object that was added.
        """
        if isinstance(node, int):
            if upload_eds:
                logger.info("Trying to read EDS from node %d", node)
                object_dictionary = import_from_node(node, self)
            node = RemoteNode(node, object_dictionary)
        self[node.id] = node
        return node

    async def aadd_node(
        self,
        node: Union[int, RemoteNode, LocalNode],
        object_dictionary: Union[str, ObjectDictionary, None] = None,
        upload_eds: bool = False,
    ) -> RemoteNode:
        """Add a remote node to the network, async variant.

        See add_node() for description
        """
        # NOTE: The async variant exists because import_from_node might block
        return await asyncio.to_thread(self.add_node, node,
                                       object_dictionary, upload_eds)

    def create_node(
        self,
        node: int,
        object_dictionary: Union[str, ObjectDictionary, None] = None,
    ) -> LocalNode:
        """Create a local node in the network.

        :param node:
            An integer representing the node ID.
        :param object_dictionary:
            Can be either a string for specifying the path to an
            Object Dictionary file or a
            :class:`canopen.ObjectDictionary` object.

        :return:
            The Node object that was added.
        """
        if isinstance(node, int):
            node = LocalNode(node, object_dictionary)
        self[node.id] = node
        return node

    def send_message(self, can_id: int, data: bytes, remote: bool = False) -> None:
        """Send a raw CAN message to the network.

        This method may be overridden in a subclass if you need to integrate
        this library with a custom backend.
        It is safe to call this from multiple threads.

        :param int can_id:
            CAN-ID of the message
        :param data:
            Data to be transmitted (anything that can be converted to bytes)
        :param bool remote:
            Set to True to send remote frame

        :raises can.CanError:
            When the message fails to be transmitted
        """
        if not self.bus:
            raise RuntimeError("Not connected to CAN bus")
        msg = can.Message(is_extended_id=can_id > 0x7FF,
                          arbitration_id=can_id,
                          data=data,
                          is_remote_frame=remote)
        # NOTE: Blocking lock. This is probably ok for async, because async
        #       only use one thread.
        with self.send_lock:
            self.bus.send(msg)
        self.check()

    def send_periodic(
        self, can_id: int, data: bytes, period: float, remote: bool = False
    ) -> PeriodicMessageTask:
        """Start sending a message periodically.

        :param can_id:
            CAN-ID of the message
        :param data:
            Data to be transmitted (anything that can be converted to bytes)
        :param period:
            Seconds between each message
        :param remote:
            indicates if the message frame is a remote request to the slave node

        :return:
            An task object with a ``.stop()`` method to stop the transmission
        """
        return PeriodicMessageTask(can_id, data, period, self.bus, remote)

    # @callback  # NOTE: called from another thread
    def notify(self, can_id: int, data: bytearray, timestamp: float) -> None:
        """Feed incoming message to this library.

        If a custom interface is used, this function must be called for each
        message read from the CAN bus.

        :param can_id:
            CAN-ID of the message
        :param data:
            Data part of the message (0 - 8 bytes)
        :param timestamp:
            Timestamp of the message, preferably as a Unix timestamp
        """
        if can_id in self.subscribers:
            self.dispatch_callbacks(self.subscribers[can_id], can_id, data, timestamp)
        self.scanner.on_message_received(can_id)

    def on_error(self, exc: BaseException) -> None:
        """This method is called to handle any exception in the callbacks."""

        # Exceptions in any callbaks should not affect CAN processing
        logger.exception("Exception in callback: %s", exc_info=exc)

    def dispatch_callbacks(self, callbacks: List[Callback], *args) -> None:
        """Dispatch a list of callbacks with the given arguments.

        :param callbacks:
            List of callbacks to call
        :param args:
            Arguments to pass to the callbacks
        """
        def task_done(task: asyncio.Task) -> None:
            """Callback to be called when a task is done."""
            self._tasks.discard(task)

            # FIXME: This section should probably be migrated to a TaskGroup.
            # However, this is not available yet in Python 3.8 - 3.10.
            try:
                if (exc := task.exception()) is not None:
                    self.on_error(exc)
            except (asyncio.CancelledError, asyncio.InvalidStateError) as exc:
                # Handle cancelled tasks and unfinished tasks gracefully
                self.on_error(exc)

        # Run the callbacks
        for callback in callbacks:
            result = callback(*args)
            if result is not None and asyncio.iscoroutine(result):
                task = asyncio.create_task(result)
                self._tasks.add(task)
                task.add_done_callback(task_done)

    def check(self) -> None:
        """Check that no fatal error has occurred in the receiving thread.

        If an exception caused the thread to terminate, that exception will be
        raised.
        """
        if self.notifier is not None:
            exc = self.notifier.exception
            if exc is not None:
                logger.error("An error has caused receiving of messages to stop")
                raise exc

    def is_async(self) -> bool:
        """Check if canopen has been connected with async"""
        return self.loop is not None

    def __getitem__(self, node_id: int) -> Union[RemoteNode, LocalNode]:
        return self.nodes[node_id]

    def __setitem__(self, node_id: int, node: Union[RemoteNode, LocalNode]):
        assert node_id == node.id
        if node_id in self.nodes:
            # Remove old callbacks
            self.nodes[node_id].remove_network()
        self.nodes[node_id] = node
        node.associate_network(self)

    def __delitem__(self, node_id: int):
        self.nodes[node_id].remove_network()
        del self.nodes[node_id]

    def __iter__(self) -> Iterator[int]:
        return iter(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)


class _UninitializedNetwork(Network):
    """Empty network implementation as a placeholder before actual initialization."""

    def __init__(self, bus: Optional[can.BusABC] = None):
        """Do not initialize attributes, by skipping the parent constructor."""

    def __getattribute__(self, name):
        raise RuntimeError("No actual Network object was assigned, "
                           "try associating to a real network first.")


#: Singleton instance
_UNINITIALIZED_NETWORK: Final[Network] = _UninitializedNetwork()


class PeriodicMessageTask:
    """
    Task object to transmit a message periodically using python-can's
    CyclicSendTask
    """

    def __init__(
        self,
        can_id: int,
        data: bytes,
        period: float,
        bus,
        remote: bool = False,
    ):
        """
        :param can_id:
            CAN-ID of the message
        :param data:
            Data to be transmitted (anything that can be converted to bytes)
        :param period:
            Seconds between each message
        :param can.BusABC bus:
            python-can bus to use for transmission
        """
        self.bus = bus
        self.period = period
        self.msg = can.Message(is_extended_id=can_id > 0x7FF,
                               arbitration_id=can_id,
                               data=data, is_remote_frame=remote)
        self._start()

    def _start(self):
        self._task = self.bus.send_periodic(self.msg, self.period)

    def stop(self):
        """Stop transmission"""
        self._task.stop()

    # @callback  # NOTE: Indirectly called from another thread via other callbacks
    def update(self, data: bytes) -> None:
        """Update data of message

        :param data:
            New data to transmit
        """
        new_data = bytearray(data)
        old_data = self.msg.data
        self.msg.data = new_data
        if hasattr(self._task, "modify_data"):
            self._task.modify_data(self.msg)
        elif new_data != old_data:
            # Stop and start (will mess up period unfortunately)
            self._task.stop()
            self._start()


class MessageListener(Listener):
    """Listens for messages on CAN bus and feeds them to a Network instance.

    :param network:
        The network to notify on new messages.
    """

    def __init__(self, network: Network):
        self.network = network

    # @callback  # NOTE: called from another thread
    def on_message_received(self, msg):
        if msg.is_error_frame or msg.is_remote_frame:
            return

        try:
            self.network.notify(msg.arbitration_id, msg.data, msg.timestamp)
        except Exception as e:
            # Exceptions in any callbaks should not affect CAN processing
            self.network.on_error(e)

    def stop(self) -> None:
        """Override abstract base method to release any resources."""


class NodeScanner:
    """Observes which nodes are present on the bus.

    Listens for the following messages:
     - Heartbeat (0x700)
     - SDO response (0x580)
     - TxPDO (0x180, 0x280, 0x380, 0x480)
     - EMCY (0x80)

    :param canopen.Network network:
        The network to use when doing active searching.
    """

    SERVICES = (0x700, 0x580, 0x180, 0x280, 0x380, 0x480, 0x80)

    def __init__(self, network: Optional[Network] = None):
        if network is None:
            network = _UNINITIALIZED_NETWORK
        self.network: Network = network
        #: A :class:`list` of nodes discovered
        self.nodes: List[int] = []

    # @callback  # NOTE: called from another thread
    def on_message_received(self, can_id: int):
        service = can_id & 0x780
        node_id = can_id & 0x7F
        if node_id not in self.nodes and node_id != 0 and service in self.SERVICES:
            self.nodes.append(node_id)

    def reset(self):
        """Clear list of found nodes."""
        self.nodes = []

    def search(self, limit: int = 127) -> None:
        """Search for nodes by sending SDO requests to all node IDs."""
        sdo_req = b"\x40\x00\x10\x00\x00\x00\x00\x00"
        for node_id in range(1, limit + 1):
            self.network.send_message(0x600 + node_id, sdo_req)
