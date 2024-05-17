from __future__ import annotations
from typing import Optional, TYPE_CHECKING
import struct
import logging
import io
import asyncio

from canopen.network import CanError
from canopen import objectdictionary
from canopen.sdo.constants import *
from canopen.sdo.exceptions import *
from canopen.sdo import io_async

if TYPE_CHECKING:
    from canopen.network import Network

logger = logging.getLogger(__name__)


class SdoClientAsyncMixin:
    """Handles async communication with an SDO server."""

    # Types found in SDOClient
    MAX_RETRIES: int
    PAUSE_BEFORE_SEND: float
    PAUSE_AFTER_SEND: float
    aresponses: asyncio.Queue
    network: Optional[Network]

    async def aon_response(self, can_id, data, timestamp):
        await self.aresponses.put(bytes(data))

    async def asend_request(self, request):
        retries_left = self.MAX_RETRIES
        while True:
            try:
                if self.PAUSE_BEFORE_SEND:
                    await asyncio.sleep(self.PAUSE_BEFORE_SEND)
                self.network.send_message(self.rx_cobid, request)
            except CanError as e:
                # Could be a buffer overflow. Wait some time before trying again
                retries_left -= 1
                if not retries_left:
                    raise
                logger.info(str(e))
                if self.PAUSE_AFTER_SEND:
                    await asyncio.sleep(self.PAUSE_AFTER_SEND)
            else:
                break

    async def aread_response(self):
        try:
            response = await asyncio.wait_for(
                self.aresponses.get(), timeout=self.RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            raise SdoCommunicationError("No SDO response received")
        res_command, = struct.unpack_from("B", response)
        if res_command == RESPONSE_ABORTED:
            abort_code, = struct.unpack_from("<L", response, 4)
            raise SdoAbortedError(abort_code)
        return response

    async def arequest_response(self, sdo_request):
        retries_left = self.MAX_RETRIES
        while True:
            await self.asend_request(sdo_request)
            # Wait for node to respond
            try:
                return await self.aread_response()
            except SdoCommunicationError as e:
                retries_left -= 1
                if not retries_left:
                    await self.aabort(0x5040000)
                    raise
                logger.warning(str(e))

    async def aabort(self, abort_code=0x08000000):
        """Abort current transfer."""
        request = bytearray(8)
        request[0] = REQUEST_ABORTED
        # TODO: Is it necessary to include index and subindex?
        struct.pack_into("<L", request, 4, abort_code)
        await self.asend_request(request)
        logger.error("Transfer aborted by client with code 0x{:08X}".format(abort_code))

    async def aupload(self, index: int, subindex: int) -> bytes:
        """May be called to make a read operation without an Object Dictionary.

        :param index:
            Index of object to read.
        :param subindex:
            Sub-index of object to read.

        :return: A data object.

        :raises canopen.SdoCommunicationError:
            On unexpected response or timeout.
        :raises canopen.SdoAbortedError:
            When node responds with an error.
        """
        async with self.lock:  # Ensure only one active SDO request per client
            async with await self.aopen(index, subindex, buffering=0) as fp:
                response_size = fp.size
                data = await fp.read()

        # If size is available through variable in OD, then use the smaller of the two sizes.
        # Some devices send U32/I32 even if variable is smaller in OD
        var = self.od.get_variable(index, subindex)
        if var is not None:
            # Found a matching variable in OD
            # If this is a data type (string, domain etc) the size is
            # unknown anyway so keep the data as is
            if var.data_type not in objectdictionary.DATA_TYPES:
                # Get the size in bytes for this variable
                var_size = len(var) // 8
                if response_size is None or var_size < response_size:
                    # Truncate the data to specified size
                    data = data[0:var_size]
        return data

    async def adownload(
        self,
        index: int,
        subindex: int,
        data: bytes,
        force_segment: bool = False,
    ) -> None:
        """May be called to make a write operation without an Object Dictionary.

        :param index:
            Index of object to write.
        :param subindex:
            Sub-index of object to write.
        :param data:
            Data to be written.
        :param force_segment:
            Force use of segmented transfer regardless of data size.

        :raises canopen.SdoCommunicationError:
            On unexpected response or timeout.
        :raises canopen.SdoAbortedError:
            When node responds with an error.
        """
        async with self.lock:  # Ensure only one active SDO request per client
            async with await self.aopen(index, subindex, "wb", buffering=7, size=len(data),
                                        force_segment=force_segment) as fp:
                await fp.write(data)

    async def aopen(self, index, subindex=0, mode="rb", encoding="ascii",
                    buffering=1024, size=None, block_transfer=False, force_segment=False, request_crc_support=True):
        """Open the data stream as a file like object.

        :param int index:
            Index of object to open.
        :param int subindex:
            Sub-index of object to open.
        :param str mode:
            ========= ==========================================================
            Character Meaning
            --------- ----------------------------------------------------------
            'r'       open for reading (default)
            'w'       open for writing
            'b'       binary mode (default)
            't'       text mode
            ========= ==========================================================
        :param str encoding:
            The str name of the encoding used to decode or encode the file.
            This will only be used in text mode.
        :param int buffering:
            An optional integer used to set the buffering policy. Pass 0 to
            switch buffering off (only allowed in binary mode), 1 to select line
            buffering (only usable in text mode), and an integer > 1 to indicate
            the size in bytes of a fixed-size chunk buffer.
        :param int size:
            Size of data to that will be transmitted.
        :param bool block_transfer:
            If block transfer should be used.
        :param bool force_segment:
            Force use of segmented download regardless of data size.
        :param bool request_crc_support:
            If crc calculation should be requested when using block transfer

        :returns:
            A file like object.
        """
        buffer_size = buffering if buffering > 1 else io.DEFAULT_BUFFER_SIZE
        if "r" in mode:
            if block_transfer:
                raise NotImplementedError("BlockUploadStream for async not implemented")
                # raw_stream = ABlockUploadStream(self, index, subindex, request_crc_support=request_crc_support)
            else:
                raw_stream = await AReadableStream.open(self, index, subindex)
            if buffering:
                buffered_stream = io_async.BufferedReader(raw_stream, buffer_size=buffer_size)
            else:
                return raw_stream
        if "w" in mode:
            if block_transfer:
                raise NotImplementedError("BlockDownloadStream for async not implemented")
                # raw_stream = ABlockDownloadStream(self, index, subindex, size, request_crc_support=request_crc_support)
            else:
                raw_stream = await AWritableStream.open(self, index, subindex, size, force_segment)
            if buffering:
                buffered_stream = io_async.BufferedWriter(raw_stream, buffer_size=buffer_size)
            else:
                return raw_stream
        if "b" not in mode:
            # Text mode
            line_buffering = buffering == 1
            # FIXME: Implement io.TextIOWrapper for async?
            raise NotImplementedError("TextIOWrapper for async not implemented")
            # return io.TextIOWrapper(buffered_stream, encoding,
            #                         line_buffering=line_buffering)
        return buffered_stream


class AReadableStream(io_async.RawIOBase):
    """File like object for reading async from a variable."""

    #: Total size of data or ``None`` if not specified
    size = None

    @classmethod
    async def open(cls, sdo_client, index, subindex=0):
        """
        :param canopen.sdo.SdoClient sdo_client:
            The SDO client to use for reading.
        :param int index:
            Object dictionary index to read from.
        :param int subindex:
            Object dictionary sub-index to read from.
        """
        logger.debug("Reading 0x%X:%d from node %d", index, subindex,
                     sdo_client.rx_cobid - 0x600)
        request = bytearray(8)
        SDO_STRUCT.pack_into(request, 0, REQUEST_UPLOAD, index, subindex)
        response = await sdo_client.arequest_response(request)

        return cls(sdo_client, index, subindex, response)

    def __init__(self, sdo_client, index, subindex, response):
        """
        :param canopen.sdo.SdoClient sdo_client:
            The SDO client to use for reading.
        :param int index:
            Object dictionary index to read from.
        :param int subindex:
            Object dictionary sub-index to read from.
        """
        self._done = False
        self.sdo_client = sdo_client
        self._toggle = 0
        self.pos = 0
        self._index = index
        self._subindex = subindex

        res_command, res_index, res_subindex = SDO_STRUCT.unpack_from(response)
        res_data = response[4:8]

        if res_command & 0xE0 != RESPONSE_UPLOAD:
            raise SdoCommunicationError("Unexpected response 0x%02X" % res_command)

        # Check that the message is for us
        if res_index != index or res_subindex != subindex:
            raise SdoCommunicationError((
                "Node returned a value for 0x{:X}:{:d} instead, "
                "maybe there is another SDO client communicating "
                "on the same SDO channel?").format(res_index, res_subindex))

        self.exp_data = None
        if res_command & EXPEDITED:
            # Expedited upload
            if res_command & SIZE_SPECIFIED:
                self.size = 4 - ((res_command >> 2) & 0x3)
                self.exp_data = res_data[:self.size]
            else:
                self.exp_data = res_data
            self.pos += len(self.exp_data)
        elif res_command & SIZE_SPECIFIED:
            self.size, = struct.unpack("<L", res_data)
            logger.debug("Using segmented transfer of %d bytes", self.size)
        else:
            logger.debug("Using segmented transfer")

    async def read(self, size=-1):
        """Read one segment which may be up to 7 bytes.

        :param int size:
            If size is -1, all data will be returned. Other values are ignored.

        :returns: 1 - 7 bytes of data or no bytes if EOF.
        :rtype: bytes
        """
        if self._done:
            return b""
        if self.exp_data is not None:
            self._done = True
            return self.exp_data
        if size is None or size < 0:
            return await self.readall()

        command = REQUEST_SEGMENT_UPLOAD
        command |= self._toggle
        request = bytearray(8)
        request[0] = command
        response = await self.sdo_client.arequest_response(request)
        res_command, = struct.unpack_from("B", response)
        if res_command & 0xE0 != RESPONSE_SEGMENT_UPLOAD:
            raise SdoCommunicationError("Unexpected response 0x%02X" % res_command)
        if res_command & TOGGLE_BIT != self._toggle:
            raise SdoCommunicationError("Toggle bit mismatch")
        length = 7 - ((res_command >> 1) & 0x7)
        if res_command & NO_MORE_DATA:
            self._done = True
        self._toggle ^= TOGGLE_BIT
        self.pos += length
        return response[1:length + 1]

    async def readinto(self, b):
        """
        Read bytes into a pre-allocated, writable bytes-like object b,
        and return the number of bytes read.
        """
        data = await self.read(7)
        b[:len(data)] = data
        return len(data)

    def readable(self):
        return True

    async def tell(self):
        return self.pos


class AWritableStream(io_async.RawIOBase):
    """File like object for writing to a variable."""

    @classmethod
    async def open(cls, sdo_client: SdoClientAsyncMixin, index, subindex=0, size=None, force_segment=False):
        """
        :param canopen.sdo.SdoClient sdo_client:
            The SDO client to use for communication.
        :param int index:
            Object dictionary index to read from.
        :param int subindex:
            Object dictionary sub-index to read from.
        :param int size:
            Size of data in number of bytes if known in advance.
        :param bool force_segment:
            Force use of segmented transfer regardless of size.
        """
        response = None
        if size is None or size > 4 or force_segment:
            # Initiate segmented download
            request = bytearray(8)
            command = REQUEST_DOWNLOAD
            if size is not None:
                command |= SIZE_SPECIFIED
                struct.pack_into("<L", request, 4, size)
            SDO_STRUCT.pack_into(request, 0, command, index, subindex)
            response = await sdo_client.arequest_response(request)

        return cls(sdo_client, index, subindex, size, force_segment, response)

    def __init__(self, sdo_client, index, subindex=0, size=None, force_segment=False, response=None):
        """
        :param canopen.sdo.SdoClient sdo_client:
            The SDO client to use for communication.
        :param int index:
            Object dictionary index to read from.
        :param int subindex:
            Object dictionary sub-index to read from.
        :param int size:
            Size of data in number of bytes if known in advance.
        :param bool force_segment:
            Force use of segmented transfer regardless of size.
        """
        self.sdo_client: SdoClientAsyncMixin = sdo_client
        self.size = size
        self.pos = 0
        self._toggle = 0
        self._exp_header = None
        self._done = False

        # Implies this: if size is None or size > 4 or force_segment
        if response:
            # Initiate segmented download
            # request = bytearray(8)
            # command = REQUEST_DOWNLOAD
            # if size is not None:
            #     command |= SIZE_SPECIFIED
            #     struct.pack_into("<L", request, 4, size)
            # SDO_STRUCT.pack_into(request, 0, command, index, subindex)
            # response = sdo_client.request_response(request)
            res_command, = struct.unpack_from("B", response)
            if res_command != RESPONSE_DOWNLOAD:
                raise SdoCommunicationError(
                    "Unexpected response 0x%02X" % res_command)
        else:
            # Expedited download
            # Prepare header (first 4 bytes in CAN message)
            command = REQUEST_DOWNLOAD | EXPEDITED | SIZE_SPECIFIED
            command |= (4 - size) << 2
            self._exp_header = SDO_STRUCT.pack(command, index, subindex)

    async def write(self, b):
        """
        Write the given bytes-like object, b, to the SDO server, and return the
        number of bytes written. This will be at most 7 bytes.
        """
        if self._done:
            raise RuntimeError("All expected data has already been transmitted")
        if self._exp_header is not None:
            # Expedited download
            if len(b) < self.size:
                # Not enough data provided
                return 0
            if len(b) > 4:
                raise AssertionError("More data received than expected")
            data = b.tobytes() if isinstance(b, memoryview) else b
            request = self._exp_header + data.ljust(4, b"\x00")
            response = await self.sdo_client.arequest_response(request)
            res_command, = struct.unpack_from("B", response)
            if res_command & 0xE0 != RESPONSE_DOWNLOAD:
                raise SdoCommunicationError(
                    "Unexpected response 0x%02X" % res_command)
            bytes_sent = len(b)
            self._done = True
        else:
            # Segmented download
            request = bytearray(8)
            command = REQUEST_SEGMENT_DOWNLOAD
            # Add toggle bit
            command |= self._toggle
            self._toggle ^= TOGGLE_BIT
            # Can send up to 7 bytes at a time
            bytes_sent = min(len(b), 7)
            if self.size is not None and self.pos + bytes_sent >= self.size:
                # No more data after this message
                command |= NO_MORE_DATA
                self._done = True
            # Specify number of bytes that do not contain segment data
            command |= (7 - bytes_sent) << 1
            request[0] = command
            request[1:bytes_sent + 1] = b[0:bytes_sent]
            response = await self.sdo_client.arequest_response(request)
            res_command, = struct.unpack("B", response[0:1])
            if res_command & 0xE0 != RESPONSE_SEGMENT_DOWNLOAD:
                raise SdoCommunicationError(
                    "Unexpected response 0x%02X (expected 0x%02X)" %
                    (res_command, RESPONSE_SEGMENT_DOWNLOAD))
        # Advance position
        self.pos += bytes_sent
        return bytes_sent

    async def close(self):
        """Closes the stream.

        An empty segmented SDO message may be sent saying there is no more data.
        """
        await super(AWritableStream, self).close()
        if not self._done and not self._exp_header:
            # Segmented download not finished
            command = REQUEST_SEGMENT_DOWNLOAD | NO_MORE_DATA
            command |= self._toggle
            # No data in this message
            command |= 7 << 1
            request = bytearray(8)
            request[0] = command
            await self.sdo_client.arequest_response(request)
            self._done = True

    def writable(self):
        return True

    async def tell(self):
        return self.pos

