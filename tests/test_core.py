from flacfetch.core.models import AudioFormat, MediaSource, Quality


def test_quality_comparison_format():
    q_lossless = Quality(format=AudioFormat.FLAC, bit_depth=16)
    q_lossy = Quality(format=AudioFormat.MP3, bitrate=320)

    # Lossless should be greater than Lossy
    assert q_lossy < q_lossless
    assert not (q_lossless < q_lossy)

def test_quality_comparison_bitdepth():
    q_24 = Quality(format=AudioFormat.FLAC, bit_depth=24)
    q_16 = Quality(format=AudioFormat.FLAC, bit_depth=16)

    assert q_16 < q_24

def test_quality_comparison_bitrate():
    q_320 = Quality(format=AudioFormat.MP3, bitrate=320)
    q_192 = Quality(format=AudioFormat.MP3, bitrate=192)

    assert q_192 < q_320

def test_quality_comparison_media():
    # WEB > CD > VINYL (as per our arbitrary logic in models.py)
    q_web = Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.WEB)
    q_cd = Quality(format=AudioFormat.FLAC, bit_depth=16, media=MediaSource.CD)

    # Check ranking in models.py: WEB=3, CD=2
    assert q_cd < q_web

