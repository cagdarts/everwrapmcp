"""Synthetic language-routing, readability and privacy regressions."""
import pytest
from everwrap.redaction import get_redactor

@pytest.mark.parametrize('text', [
    'İşimde sıkışmış hissediyorum ve yeteneklerime uygun bir rol arıyorum.',
    'Düşüncelerimi yazarak düzenlemek bana iyi geliyor.',
    'Kişisel Gelişim ve Kariyer Planlama',
    'I feel stuck at work and want a role that fits my strengths.',
    'chatbot için olay tabanlı mimari kullan.',
])
def test_ordinary_reflections_remain_readable(text):
    assert get_redactor(False).sanitize_text(text) == text

@pytest.mark.parametrize('text, forbidden', [
    ('Bugün Ayşe Yılmaz ile konuştum.', ['Ayşe', 'Yılmaz']),
    ('I spoke with Alex Smith. Sonra Ayşe Yılmaz ile görüştüm.', ['Alex', 'Smith', 'Ayşe', 'Yılmaz']),
    ('Ayşe Yılmaz ile konuştum. Email alex@example.com. şifre: synthetic-pass-927',
     ['Ayşe', 'Yılmaz', 'alex@example.com', 'synthetic-pass-927']),
    ('Düşüncelerimi yazıyorum. Telefon: +90 555 123 45 67', ['555']),
    ('Adres: Bahar Mahallesi Çiçek Sokak No: 18 Ankara', ['Bahar', 'Çiçek', '18', 'Ankara']),
])
def test_sensitive_values_in_both_languages(text, forbidden):
    output = get_redactor(False).sanitize_text(text)
    assert all(word not in output for word in forbidden)


def test_long_turkish_sentence_tail_is_analyzed():
    text = 'düşüncelerimi yazarak düzenlemek bana iyi geliyor ' * 90 + 'Ayşe Yılmaz ile konuştum'
    output = get_redactor(False).sanitize_text(text)
    assert 'Ayşe' not in output and 'Yılmaz' not in output


def test_ambiguous_language_is_withheld():
    from types import SimpleNamespace
    from everwrap.language import LanguageAwareAnalyzer
    analyzer = object.__new__(LanguageAwareAnalyzer)
    analyzer.detector = SimpleNamespace(compute_language_confidence_values=lambda _: [])
    assert analyzer.languages('Alex') == ()


def test_detector_failure_does_not_expose_input():
    from types import SimpleNamespace
    from everwrap.language import LanguageAwareAnalyzer
    from everwrap.service import ProcessingBlocked
    analyzer = object.__new__(LanguageAwareAnalyzer)
    def broken(text):
        raise RuntimeError(text)
    analyzer.tokens = broken
    with pytest.raises(ProcessingBlocked) as exc:
        analyzer.analyze(text='synthetic-private-canary')
    assert 'synthetic-private-canary' not in str(exc.value)


def test_missing_turkish_model_fails_closed(tmp_path, monkeypatch):
    import en_core_web_md
    from presidio_analyzer import AnalyzerEngine
    from everwrap import language
    monkeypatch.setattr(language, 'MODEL_PATH', tmp_path / 'missing')
    with pytest.raises((OSError, ValueError)):
        language.LanguageAwareAnalyzer(AnalyzerEngine(), en_core_web_md.load())
