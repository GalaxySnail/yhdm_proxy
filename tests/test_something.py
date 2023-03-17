import pytest
import trio
import httpx


@pytest.mark.anyio
async def test_request(static_url, client):
    response = await client.get(static_url + "/abc.html")

    assert response.status_code == 200
    assert response.text == "<h1>hello world</h1>\n"


@pytest.fixture(params=["stream01.m3u8"])
def m3u8(request):
    return request.param


@pytest.fixture
def m3u8_url(static_url, m3u8):
    return f"{static_url}/{m3u8}"


@pytest.fixture
def m3u8_proxy_url(yhdm_proxy_url, m3u8_url):
    return f"{yhdm_proxy_url}/m3u8/{m3u8_url}"


@pytest.fixture
def m3u8_expected(yhdm_proxy_url, m3u8):
    if m3u8 == "stream01.m3u8":
        return (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:10\n"
            "\n"
            "#EXTINF:9.009,\n"
            f"{yhdm_proxy_url}/png/http://media.example.com/first.ts\n"
            "#EXTINF:9.009,\n"
            f"{yhdm_proxy_url}/png/http://media.example.com/second.ts\n"
            "#EXTINF:3.003,\n"
            f"{yhdm_proxy_url}/png/http://media.example.com/third.ts\n"
        )
    assert False, "Unreachable"


@pytest.mark.anyio
async def test_m3u8(m3u8_url, m3u8_proxy_url, client, m3u8_expected):
    response: httpx.Response
    response = await client.get(m3u8_url)
    origin_content_type = response.headers["Content-Type"]

    response = await client.get(m3u8_proxy_url)
    proxy_content_type = response.headers["Content-Type"]
    proxy_text = response.text

    assert response.status_code == 200
    assert origin_content_type == proxy_content_type
    assert proxy_text == m3u8_expected
