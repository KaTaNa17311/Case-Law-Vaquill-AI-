"""Deterministic presentation only: non-whitespace characters must stay identical."""
import hashlib
import re

# Only explicit spacing patterns. No dictionary, spelling correction or language model.
# Unrecognised joined words remain untouched rather than risking a guessed split.
PHRASES = (
    'within a period', 'from the date', 'copy of this order', 'writ petition',
    'learned counsel', 'learned advocate', 'learned AGA', 'accept notice',
    'final hearing', 'preliminary hearing', 'the petitioner', 'the respondents',
    'the respondent', 'the application', 'the order', 'the court', 'the date',
    'this petition', 'this order', 'an application', 'a mandamus', 'a copy',
    'as against', 'with consent', 'for final', 'for registration', 'in part',
    'six weeks', 'four weeks', 'of learned', 'order passed', 'has been',
    'is permitted', 'is directed', 'on behalf', 'is taken', 'to pass',
    'under Section', 'under Articles', 'to consider', 'has not', 'have been',
)


def fingerprint(text):
    return hashlib.sha256(re.sub(r'\s', '', text).encode('utf-8')).hexdigest()


def _space_phrase(match, phrase):
    original = match.group(0)
    lengths = [len(word) for word in phrase.split()]
    at = 0
    pieces = []
    for length in lengths:
        pieces.append(original[at:at + length])
        at += length
    return ' '.join(pieces)


def present(raw):
    if not isinstance(raw, str):
        raise TypeError('Judgment text must be a string')
    text = raw.replace('\r\n', '\n').replace('\r', '\n')
    for phrase in sorted(PHRASES, key=lambda p: -len(p)):
        joined = phrase.replace(' ', '')
        text = re.sub(r'(?<!\w)' + re.escape(joined) + r'(?!\w)',
                      lambda m, p=phrase: _space_phrase(m, p), text, flags=re.I)
    # Word/sentence boundaries only; preserve numeric dates, decimals and initials.
    text = re.sub(r'([,;:])(?=[A-Za-z])', r'\1 ', text)
    text = re.sub(r'(?<=[a-z])\.(?=[A-Z][a-z])', '. ', text)
    text = re.sub(r'(?m)^(\s*\d{1,3}[.)]+)(?=[A-Za-z‘’])', r'\1 ', text)
    text = re.sub(r'(?<=\])(?=[A-Za-z])', ' ', text)
    text = re.sub(r'[\t \u00a0]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.split('\n'))
    # Markdown/OCR layout markers are retained in text; the UI represents them as styling.
    text = re.sub(r'(?m)(?<!\n)\n(?=(?:\[SECTION\]|\[TITLE\]|ORDER\b|JUDGMENT\b|BETWEEN\b|AND\b|\d{1,3}[.)]+\s))', '\n\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if fingerprint(raw) != fingerprint(text):
        raise ValueError('Formatting would change non-whitespace characters')
    blocks = []
    for paragraph in re.split(r'\n\s*\n', text):
        if not paragraph.strip():
            continue
        heading = paragraph.split('\n', 1)[0]
        is_heading = bool(re.match(r'^(?:\[(?:SECTION|TITLE)\]\s*#*\s*|#{1,6}\s*)', heading)) or bool(re.fullmatch(r"[A-Z0-9 .,'’()/|\[\]%-]{3,100}", paragraph))
        blocks.append({'kind': 'heading' if is_heading else 'paragraph', 'text': paragraph})
    return {
        'formatted_text': text,
        'presentation_blocks': blocks,
        'formatting': {
            'version': 'whitespace-only-v1',
            'non_whitespace_unchanged': True,
            'raw_fingerprint': fingerprint(raw),
            'formatted_fingerprint': fingerprint(text),
            'spelling_correction': False,
        },
    }
