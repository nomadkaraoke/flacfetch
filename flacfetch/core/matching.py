import re
from difflib import SequenceMatcher


def clean_filename(filename: str) -> str:
    # Remove file extension
    if '.' in filename:
        filename = filename.rsplit('.', 1)[0]
    # Remove track numbers like "01 - ", "01. ", "1-", "A1 "
    filename = re.sub(r'^(\d{1,3}[ .-]+)+', '', filename)
    filename = re.sub(r'^[A-D]\d{1,2}[ .-]+', '', filename)
    # Remove " - " separators which might remain
    filename = filename.replace(' - ', ' ')
    return filename.strip()

def calculate_match_score(track_title: str, filename: str) -> float:
    """
    Returns a score 0.0 - 1.0 indicating how well the filename matches the track title.
    1.0 = Exact match (ignoring case/punctuation)
    """
    cleaned_name = clean_filename(filename).lower()
    target = track_title.lower()

    # Exact match check
    if cleaned_name == target:
        return 1.0

    # Exact word match check (e.g. "Tonight" in "Tonight (Acoustic)")
    # But NOT "Tonight" in "Goodbye Tonight" - wait, actually "Goodbye Tonight" IS a match if user asks for "Tonight".
    # But we want to penalize it.

    # Regex for exact phrase with word boundaries
    import re
    escaped_target = re.escape(target)
    if re.search(r'\b' + escaped_target + r'\b', cleaned_name):
        # Found exact phrase
        # Score based on ratio of lengths: (target_len / cleaned_len)
        # "Tonight" (7) vs "Tonight (Acoustic)" (18) -> 0.38
        # This seems too low for a good match.
        # Let's boost it if it STARTS with the target.

        base_score = len(target) / len(cleaned_name)

        if cleaned_name.startswith(target):
            # Bonus for starting with the title (likely the correct track)
            return 0.8 + (0.2 * base_score)
        else:
            # Present but not at start (e.g. "Goodbye Tonight")
            return 0.5 + (0.4 * base_score)

    # Fuzzy match for minor typos
    return SequenceMatcher(None, target, cleaned_name).ratio()

