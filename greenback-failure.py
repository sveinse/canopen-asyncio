import asyncio
import canopen
from canopen.objectdictionary import ObjectDictionary
from canned import Canned, set_loglevels
from canned.funcs import CanNode

set_loglevels(['DEBUG'])


async def loop():

    while True:
        await asyncio.sleep(1)
        print("                             task")


async def main_canopen():

    network = canopen.Network()
    loop=asyncio.get_event_loop()
    # observer = CannedObserver()
    # cancfg = CanConfig(loop)
    # network.listeners.extend([observer, cancfg])
    network.connect(
        interface="pcan",
        bitrate=1000000,
        receive_own_message=True,
        loop=loop,
    )

    od = ObjectDictionary()
    node = network.add_node(2, od)

    # asyncio.create_task(aloop())

    # WORKING
    await node.sdo.aupload(0x1000, 0x00)


async def main_canned():

    # Use context to connect
    with Canned.connect(
        interface="pcan",
        bitrate=1000000,
        receive_own_message=True
    ) as canned:

        # asyncio.create_task(loop())

        # # WORKS
        # node = canned.create_node(2, factory=CanNode)
        # await node.sdo.aupload(0x1000, 0x00)

        # # WORKS
        # node = canned.create_node(2, factory=CanNodePower)
        # await asyncio.wait_for(node.sdo.aupload(0x1000, 0x00), timeout=10)

        # FAILS
        await canned.get_devicetype(2)

        # # WORKS
        # node = canned.create_node(2, factory=CanNode)
        # await node.sdo["Device Type"]


if __name__ == "__main__":
    asyncio.run(main_canned())