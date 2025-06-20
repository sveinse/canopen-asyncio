from canopen_asyncio.network import Network, NodeScanner
from canopen_asyncio.node import LocalNode, RemoteNode
from canopen_asyncio.objectdictionary import (
    ObjectDictionary,
    ObjectDictionaryError,
    export_od,
    import_od,
)
from canopen_asyncio.profiles.p402 import BaseNode402
from canopen_asyncio.sdo import SdoAbortedError, SdoCommunicationError

try:
    from canopen_asyncio._version import version as __version__
except ImportError:
    # package is not installed
    __version__ = "unknown"

__all__ = [
    "Network",
    "NodeScanner",
    "RemoteNode",
    "LocalNode",
    "SdoCommunicationError",
    "SdoAbortedError",
    "import_od",
    "export_od",
    "ObjectDictionary",
    "ObjectDictionaryError",
    "BaseNode402",
]
__pypi_url__ = "https://pypi.org/project/canopen-asyncio/"

Node = RemoteNode
