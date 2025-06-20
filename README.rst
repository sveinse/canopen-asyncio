CANopen for Python, asyncio port
================================

A Python implementation of the `CANopen standard`_.
The aim of the project is to support the most common parts of the CiA 301
standard in a simple Pythonic interface. It is mainly targeted for testing and
automation tasks rather than a standard compliant master implementation.

The library supports Python 3.9 or newer.

This library is the asyncio port of CANopen. It is a fork of the upstream
canopen_ library, adding support for running in an asyncio environment.

NOTE
-----

There is ongoing work to merge this asyncio port back into the upstream
canopen library. This is not yet complete, and this package was created to
be able to use the asyncio port in the meantime. When the merge is
complete, this package will be deprecated and the upstream library will
support asyncio natively.

See `canopen asyncio issue`_ and `canopen asyncio PR`_ for more information
about the merge.


Features
--------

The library is mainly meant to be used as a master.

* NMT master
* SDO client
* PDO producer/consumer
* SYNC producer
* EMCY consumer
* TIME producer
* LSS master
* Object Dictionary from EDS
* 402 profile support

Incomplete support for creating slave nodes also exists.

* SDO server
* PDO producer/consumer
* NMT slave
* EMCY producer
* Object Dictionary from EDS


Installation
------------

Install from PyPI_ using :code:`pip`::

    $ pip install canopen-asyncio

Install from latest ``master`` on GitHub::

    $ pip install https://github.com/sveinse/canopen-asyncio/archive/main.zip

If you want to be able to change the code while using it, clone it then install
it in `develop mode`_::

    $ git clone https://github.com/sveinse/canopen-asyncio.git
    $ cd canopen-asyncio
    $ pip install -e .

Unit tests can be run using the pytest_ framework::

    $ pip install -r requirements-dev.txt
    $ pytest -v

You can also use :code:`unittest` standard library module::

    $ python3 -m unittest discover test -v


Documentation
-------------

**NOTE:** The documentation is not yet updated for the asyncio port. These docs
are for the upstream canopen library.

Documentation can be found on Read the Docs:

http://canopen.readthedocs.io/en/latest/

It can also be generated from a local clone using Sphinx_::

    $ pip install -r doc/requirements.txt
    $ make -C doc html


Hardware support
----------------

This library supports multiple hardware and drivers through the python-can_ package.
See `the list of supported devices <https://python-can.readthedocs.io/en/stable/configuration.html#interface-names>`_.

It is also possible to integrate this library with a custom backend.


Asyncio port
------------

To minimize the impact of the async changes, this port is designed to use the
existing synchronous backend of the library. This means that the library
uses :code:`asyncio.to_thread()` for many asynchronous operations.

This port remains compatible with using it in a regular non-asyncio
environment. This is selected with the `loop` parameter in the
:code:`Network` constructor. If you pass a valid asyncio event loop, the
library will run in async mode. If you pass `loop=None`, it will run in
regular blocking mode. It cannot be used in both modes at the same time.


Difference between async and non-async version
----------------------------------------------

This port have some differences with the upstream non-async version of canopen.

* Minimum python version is 3.9, while the upstream version supports 3.8.

* The :code:`Network` accepts additional parameters than upstream. It accepts
  :code:`loop` which selects the mode of operation. If :code:`None` it will
  run in blocking mode, otherwise it will run in async mode. It supports
  providing a custom CAN :code:`notifier` if the CAN bus will be shared by
  multiple protocols.

* The :code:`Network` class can be (and should be) used in an async context
  manager. This will ensure the network will be automatically disconnected when
  exiting the context. See the example below.

* Most async functions follow an "a" prefix naming scheme.
  E.g. the async variant for :code:`SdoClient.download()` is available
  as :code:`SdoClient.adownload()`.

* Variables in the regular canopen library uses properties for getting and
  setting. This is replaced with awaitable methods in the async version.

      var = sdo['Variable'].raw  # synchronous
      sdo['Variable'].raw = 12   # synchronous

      var = await sdo['Variable'].get_raw()  # async
      await sdo['Variable'].set_raw(12)      # async

* Installed :code:`ensure_not_async()` sentinel guard in functions which
  prevents calling blocking functions in async context. It will raise the
  exception :code:`RuntimeError` "Calling a blocking function" when this
  happen. If this is encountered, it is likely that the code is not using the
  async variants of the library.

* The mechanism for CAN bus callbacks have been changed. Callbacks might be
  async, which means they cannot be called immediately. This affects how
  error handling is done in the library.

* The callbacks to the message handlers have been changed to be handled by
  :code:`Network.dispatch_callbacks()`. They are no longer called with any
  locks held, as this would not work with async. This affects:

    * :code:`PdoMaps.on_message`
    * :code:`EmcyConsumer.on_emcy`
    * :code:`NtmMaster.on_heartbaet`

* SDO block upload and download is not yet supported in async mode.

* :code:`ODVariable.__len__()` returns 64 bits instead of 8 bits to support
  truncated 24-bits integers, see #436

* :code:`BaseNode402` does not work with async

* :code:`LssMaster` does not work with async, except :code:`LssMaster.fast_scan()`

* :code:`Bits` is not working in async


Quick start
-----------

Here are some quick examples of what you can do with the async port:

.. code-block:: python

    import asyncio
    import canopen
    import can

    async def my_node(network, nodeid, od):

        # Create the node object and load the OD
        node = network.add_node(nodeid, od)

        # Read a variable using SDO
        device_name = await node.sdo['Manufacturer device name'].aget_raw()
        vendor_id = await node.sdo[0x1018][1].aget_raw()

        # Write a variable using SDO
        await node.sdo['Producer heartbeat time'].aset_raw(1000)

        # Read the PDOs from the remote
        await node.tpdo.aread()
        await node.rpdo.aread()

        # Set the module state
        node.nmt.state = 'OPERATIONAL'

        while True:

            # Wait for TPDO 1
            t = await node.tpdo[1].await_for_reception(1)
            if not t:
                continue

            # Get the TPDO 1 value
            speed = node.tpdo[1]['Velocity actual value'].phys
            val = node.tpdo['Some group.Some subindex'].raw

            # Sleep a little
            await asyncio.sleep(0.2)

            # Send RPDO 1 with some data
            node.rpdo[1]['Some variable'].phys = 42
            node.rpdo[1].transmit()

    async def main():

        # Connect to the CAN bus
        # Arguments are passed to python-can's can.Bus() constructor
        # (see https://python-can.readthedocs.io/en/latest/bus.html).
        # Note the loop parameter to enable asyncio operation
        #
        # Connect alternative interfaces:
        # connect(interface='socketcan', channel='can0')
        # connect(interface='kvaser', channel=0, bitrate=250000)
        # connect(interface='pcan', channel='PCAN_USBBUS1', bitrate=250000)
        # connect(interface='ixxat', channel=0, bitrate=250000)
        # connect(interface='vector', app_name='CANalyzer', channel=0, bitrate=250000)
        # connect(interface='nican', channel='CAN0', bitrate=250000)
        loop = asyncio.get_running_loop()
        async with canopen.Network(loop=loop).connect(
                interface='pcan', bitrate=1000000) as network:

            # Create two independent tasks for two nodes 51 and 52 which will run concurrently
            task1 = asyncio.create_task(my_node(network, 51, '/path/to/object_dictionary.eds'))
            task2 = asyncio.create_task(my_node(network, 52, '/path/to/object_dictionary.eds'))

            # Wait for both to complete (which will never happen)
            await asyncio.gather((task1, task2))

    asyncio.run(main())


Debugging
---------

If you need to see what's going on in better detail, you can increase the
logging_ level:

.. code-block:: python

    import logging
    logging.basicConfig(level=logging.DEBUG)


.. _PyPI: https://pypi.org/project/canopen-asyncio/
.. _canopen: https://pypi.org/project/canopen/
.. _CANopen standard: https://www.can-cia.org/can-knowledge
.. _python-can: https://python-can.readthedocs.org/en/stable/
.. _Sphinx: http://www.sphinx-doc.org/
.. _develop mode: https://packaging.python.org/distributing/#working-in-development-mode
.. _logging: https://docs.python.org/3/library/logging.html
.. _pytest: https://docs.pytest.org/
.. _canopen asyncio issue: https://github.com/canopen-python/canopen/issues/272
.. _canopen asyncio pr: https://github.com/canopen-python/canopen/pull/359
