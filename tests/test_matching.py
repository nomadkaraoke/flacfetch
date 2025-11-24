from flacfetch.core.matching import calculate_match_score


def test_exact_match():
    assert calculate_match_score("Tonight", "06 - Tonight.flac") == 1.0
    assert calculate_match_score("Tonight", "Tonight.mp3") == 1.0

def test_phrase_match():
    # "Tonight" is in "Tonight (Acoustic)"
    score = calculate_match_score("Tonight", "Tonight (Acoustic).flac")
    assert score > 0.7
    assert score < 1.0

def test_partial_word_mismatch():
    # "Tonight" is NOT in "Goodbye Tonight" as a standalone phrase match in our logic?
    # Actually regex \bTonight\b matches "Goodbye Tonight".
    # But cleaning removes "Goodbye"? No.
    # "Goodbye Tonight" cleaned is "Goodbye Tonight".
    # Match score should be lower because of length difference.

    score_exact = calculate_match_score("Tonight", "Tonight.flac")
    score_longer = calculate_match_score("Tonight", "Goodbye Tonight.flac")

    assert score_exact > score_longer

def test_no_match():
    score = calculate_match_score("Tonight", "Tomorrow.flac")
    assert score < 0.5

