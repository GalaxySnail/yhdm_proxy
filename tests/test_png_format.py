import pytest

from yhdm_proxy._png_format import PNGChunkType

def test_png_enum():
    assert PNGChunkType(b"IHDR") is PNGChunkType.IHDR
    assert PNGChunkType(b"tEXt") is PNGChunkType.tEXt

    with pytest.raises(ValueError):
        PNGChunkType(b"IhDR")
    with pytest.raises(ValueError):
        PNGChunkType(b"tEXT")

    assert PNGChunkType.IHDR.name == "IHDR"
    assert PNGChunkType.tEXt.name == "tEXt"

    assert PNGChunkType.sRGB.value == b"sRGB"
    assert PNGChunkType.tIME.value == b"tIME"

