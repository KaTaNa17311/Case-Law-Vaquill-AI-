import random
import string
from backend.presentation import present, fingerprint


def test_spacing_only_keeps_ocr_errors_and_all_characters():
    raw="[SECTION] ## ORDER\n\n2).This writpetition was filed asking for amandamus.\n\nTHR HON’BLE MR. JUSTICK A.N. VENUGOPALA GOWDA|\nwithinaperiod of sixweeks. 19.03.2010;Section(5)"
    result=present(raw)
    assert 'writ petition' in result['formatted_text']
    assert 'a mandamus' in result['formatted_text']
    assert 'within a period' in result['formatted_text']
    assert 'six weeks' in result['formatted_text']
    assert 'THR HON’BLE MR. JUSTICK' in result['formatted_text']
    assert '19.03.2010' in result['formatted_text']
    assert fingerprint(raw)==fingerprint(result['formatted_text'])


def test_case_preserved_and_no_guessed_word_changes():
    raw='THEPETITIONER hasnot COCONSICAEetne McDonald SHANWAZ A.S.BOPANNA '
    result=present(raw)['formatted_text']
    assert 'THE PETITIONER' in result
    assert 'COCONSICAEetne McDonald SHANWAZ A.S.BOPANNA' in result
    assert present(result)['formatted_text']==result


def test_unicode_punctuation_and_dates_unchanged():
    raw='₹१,२३४.५० §5 3[RD]DAY 226/227 αβ\tதமிழ்\nಕನ್ನಡ\n\n\n\nhttps://court.example/a?b=2&c=3\r\n'
    result=present(raw)
    assert fingerprint(raw)==fingerprint(result['formatted_text'])
    assert 'https://court.example/a?b=2&c=3' in result['formatted_text']
    assert '3[RD] DAY' in result['formatted_text']


def test_empty_input_and_deterministic_invariant_fuzz():
    assert present('')['formatted_text']==''
    generator=random.Random(17)
    for i in range(100):
        raw=''.join(generator.choice(string.printable+'₹§ಕನ್ನಡ') for _ in range(300))
        result=present(raw)
        assert fingerprint(raw)==fingerprint(result['formatted_text'])
        assert fingerprint(''.join(b['text'] for b in result['presentation_blocks']))==fingerprint(raw)
