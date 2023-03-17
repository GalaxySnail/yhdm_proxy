import os
import ipaddress
import subprocess
from pathlib import Path

import pytest
import trio
import httpx

import yhdm_proxy


@pytest.fixture(scope="session", autouse=True)
def set_no_proxy():
    os.environ["NO_PROXY"] = "localhost"


def pytest_addoption(parser):
    parser.addoption(
        "--port", type=int, default=15000,
        help="Specify alternate port for static_server [default: %(default)s]",
    )


@pytest.fixture(scope="session")
def static_server_port(request):
    return request.config.getoption("--port")


@pytest.fixture(scope="session")
def static_server(static_server_port):
    # TODO flask is not designed for testing, find a better server
    static_server = Path(__file__).parent / "static_server.py"
    port = str(static_server_port)
    proc = subprocess.Popen(
        ["flask", "--app", static_server, "run", "-p", port],
        stdin=subprocess.PIPE,
    )

    yield proc

    if os.name == "posix":
        proc.send_signal(2)
    else:
        # on Windows, signal.CTRL_C_EVENT will also kill parent process
        # https://stackoverflow.com/q/66143640/14723771
        proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def static_url(static_server, static_server_port):
    return f"http://localhost:{static_server_port}/static"


@pytest.fixture
def client():
    return httpx.AsyncClient()


@pytest.fixture(scope="session")
def anyio_backend():
    return "trio"


@pytest.fixture(scope="module")
@pytest.mark.anyio
async def yhdm_proxy_server():
    nursery: trio.Nursery
    async with trio.open_nursery() as nursery:
        listeners = await nursery.start(yhdm_proxy.serve)
        yield listeners
        nursery.cancel_scope.cancel()


@pytest.fixture(params=["ipv4", "ipv6"])
def ip_version(request):
    return request.param


@pytest.fixture
def yhdm_proxy_url(ip_version, yhdm_proxy_server):
    listener: trio.SocketListener
    for listener in yhdm_proxy_server:
        addr, port, *_ = listener.socket.getsockname()
        ip = ipaddress.ip_address(addr)
        if ip_version == "ipv4" and ip == ipaddress.IPv4Address("127.0.0.1"):
            yield f"http://{ip}:{port}"
            return
        if ip_version == "ipv6" and ip == ipaddress.IPv6Address("::1"):
            yield f"http://[{ip}]:{port}"
            return
    else:
        assert False, "Unreachable"
