"""Local, best-effort Presidio redaction. No remote inference or model downloads.

Selected local statistical NER plus multilingual patterns are best-effort,
not a guarantee that every sensitive span will be found.
"""

from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import unicodedata

from .service import ProcessingBlocked


MAX_TEXT = 100_000
MONTHS = (r"January|February|March|April|May|June|July|August|September|October|November|December|"
          r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
          r"Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık|"
          r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|"
          r"janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|"
          r"Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember")

# Matched spans are passed through Presidio's anonymizer, never manually removed
# from only one output surface. High-recall label patterns may mask extra text.
PATTERNS = {
    "SECRET": [
        r'''\b(?:[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|pwd|secret|token|contraseña|contrasena|mot[ \t]+de[ \t]+passe|passwort|şifre|sifre|parola|api[ \t]+anahtarı)[ \t]*[=:][ \t]*(?:"[^"\n]*"|'[^'\n]*'|[^\s;,]+)''',
        r"\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]+",
        r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16})\b",
        r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+",
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )?PRIVATE KEY-----",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    ],
    "PERSON": [
        r"\b(?:my name is|adım|ismim)[ \t]+[\p{L}\p{M}'’\-]+(?:[ \t]+[\p{L}\p{M}'’\-]+){0,2}",
        r"(?:^|\n)[ \t]*(?:full name|name|nombre(?:[ \t]+completo)?|nom(?:[ \t]+complet)?|vollständiger[ \t]+name|ad soyad|ad[ıi][ \t]+soyad[ıi]|isim|adım|ismim)[ \t]*[:=][ \t]*[^\n;]{1,200}",
    ],
    "PHONE_NUMBER": [
        r"\b(?:teléfono|telefono|téléphone|telephone|telefon|handy|móvil|mobile)[ \t]*[:=][ \t]*\+?(?:\d[ \t().-]*){6,15}\d\b",
    ],
    "DATE_TIME": [
        rf"\b\d{{1,2}}(?:\.|[ \t]+de)?[ \t]+(?:{MONTHS})(?:[ \t]+(?:de[ \t]+)?\d{{4}})?\b",
        rf"\b\d{{1,2}}[ \t]+(?:{MONTHS})(?:[ \t]+\d{{4}})?\b",
        rf"\b(?:{MONTHS})[ \t]+\d{{1,2}}(?:st|nd|rd|th)?(?:,?[ \t]+\d{{4}})?\b",
        r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b",
        r"\b(?:birthday|date of birth|dob|born on|fecha[ \t]+de[ \t]+nacimiento|cumpleaños|date[ \t]+de[ \t]+naissance|anniversaire|geburtsdatum|geburtstag|doğum[ \t]+(?:günüm|günü|tarihim|tarihi))[ \t]*[:=]?[ \t]*[^\n;]{1,100}",
    ],
    "LOCATION": [
        r"(?:^|\n)[ \t]*(?:home address|postal address|address|dirección(?:[ \t]+postal)?|domicilio|adresse(?:[ \t]+postale)?|anschrift|wohnadresse|adres|ev adresi|adresim)[ \t]*[:=][ \t]*[^\n;]{1,300}",
        r"\b\d{1,6}[A-Za-z]?[ \t]+(?:[\p{L}\d.'’\-]+[ \t]+){0,7}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Place|Pl|Court|Ct|Square|Sq|Terrace|Highway|Hwy|Parkway|Pkwy)\b[^\n;]{0,120}",
        r"\b[\p{L}'’\-]+(?:[ \t]+[\p{L}'’\-]+){0,3}[ \t]+(?:Mahallesi|Mah\.|Caddesi|Cad\.|Sokağı|Sok\.)[^\n;]{0,200}",
    ],
}


def normalize_text(text):
    if type(text) is not str or len(text) > MAX_TEXT:
        raise ProcessingBlocked("Invalid redaction input.")
    # Decode bounded layers of character entities and remove format characters
    # which can split recognizer patterns (e.g. zero-width spaces in tokens).
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    if len(text) > MAX_TEXT:
        raise ProcessingBlocked("Redaction input exceeds bounds.")
    return text


class PlainText(HTMLParser):
    BLOCK = {"div", "p", "br", "li", "tr", "td", "h1", "h2", "h3", "pre", "hr"}
    HIDDEN = {"script", "style", "en-crypt", "iframe", "object", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.hidden_depth:
            if tag not in {"br", "img", "hr", "input", "en-media", "en-todo"}:
                self.hidden_depth += 1
        elif tag in self.HIDDEN:
            self.hidden_depth = 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.hidden_depth and tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def markup_to_text(text):
    if type(text) is not str or len(text) > MAX_TEXT:
        raise ProcessingBlocked("Invalid markup input.")
    # HTMLParser is inert: no DTD/entity resolution, resource loading, or execution.
    parser = PlainText()
    parser.feed(text)
    parser.close()
    return normalize_text("".join(parser.parts).strip())


class PresidioRedactor:
    def __init__(self, languages=("en", "tr")):
        import spacy
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import SpacyNlpEngine
        from presidio_anonymizer import AnonymizerEngine

        from .packs import PACKS
        model_name = PACKS["en"]["model"]
        engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": model_name}])
        # Load an installed package directly. Do not use the provider's automatic
        # download path when a model is absent during a sensitive-content request.
        if "en" in languages:
            import importlib
            english = importlib.import_module(model_name).load()
        else:
            english = spacy.blank("en")
        engine.nlp = {"en": english}
        self.analyzer = AnalyzerEngine(nlp_engine=engine, supported_languages=["en"])
        for entity, patterns in PATTERNS.items():
            self.analyzer.registry.add_recognizer(PatternRecognizer(
                supported_entity=entity, name=f"EverWrap{entity}",
                patterns=[Pattern(name=f"{entity}_{i}", regex=pattern, score=0.85)
                          for i, pattern in enumerate(patterns)],
            ))
        from .language import LanguageAwareAnalyzer
        self.analyzer = LanguageAwareAnalyzer(self.analyzer, engine.nlp["en"], languages)
        self.anonymizer = AnonymizerEngine()

    def sanitize_text(self, text):
        return self._sanitize(text)

    def sanitize_window(self, text, start, end):
        # Selection offsets refer to already normalized local text.
        if normalize_text(text) != text or not 0 <= start <= end <= len(text):
            raise ProcessingBlocked("Invalid redaction window.")
        return self._sanitize(text, (start, end))

    def _sanitize(self, text, selection=None):
        from presidio_analyzer import RecognizerResult
        from presidio_anonymizer.entities import OperatorConfig

        text = normalize_text(text)
        if not text:
            return text
        matches = self.analyzer.analyze(text=text, language="en", score_threshold=0.35)
        if not getattr(self, 'mask_dates', True):
            matches = [match for match in matches if match.entity_type != 'DATE_TIME']
        # Union overlapping spans so a shorter, higher-confidence result cannot
        # leave part of a longer sensitive value visible.
        merged = []
        for match in sorted(matches, key=lambda m: (m.start, m.end)):
            if not 0 <= match.start < match.end <= len(text):
                raise ProcessingBlocked("Invalid detector span.")
            if merged and match.start < merged[-1].end:
                prior = merged[-1]
                prior.end = max(prior.end, match.end)
                if "SECRET" in (prior.entity_type, match.entity_type):
                    prior.entity_type = "SECRET"
                elif prior.entity_type != match.entity_type:
                    prior.entity_type = "REDACTED"
            else:
                merged.append(RecognizerResult(match.entity_type, match.start, match.end, 1.0))
        if selection is not None:
            start, end = selection
            merged = [RecognizerResult(m.entity_type, max(m.start, start) - start,
                                       min(m.end, end) - start, 1.0)
                      for m in merged if m.start < end and m.end > start]
            text = text[start:end]
        operators = {m.entity_type: OperatorConfig("replace", {"new_value": f"[{m.entity_type}]"})
                     for m in merged}
        output = self.anonymizer.anonymize(text=text, analyzer_results=merged,
                                          operators=operators).text
        if type(output) is not str or len(output) > MAX_TEXT:
            raise ProcessingBlocked("Invalid redaction output.")
        return output

    def sanitize_markup(self, text):
        return self.sanitize_text(markup_to_text(text))


@lru_cache(maxsize=6)
def get_redactor(mask_dates=True, languages=("en", "tr")):
    if not mask_dates:
        from copy import copy
        redactor = copy(get_redactor(True, languages))
        redactor.mask_dates = False
        return redactor
    return PresidioRedactor(languages)


@lru_cache(maxsize=6)
def _worker(mask_dates, languages, idle_seconds):
    from .redaction_worker import WorkerRedactor
    return WorkerRedactor(mask_dates, languages, idle_seconds=idle_seconds)


def redactor_for(mask_dates=True, languages=("en", "tr")):
    """In-process redactor by default; an idle-unloading worker when configured.

    EVERWRAP_REDACTION_WORKER_IDLE=<seconds> (set in the remote image) keeps the
    models in a child process that exits after that many idle seconds.
    """
    import os
    idle = os.environ.get("EVERWRAP_REDACTION_WORKER_IDLE")
    if not idle:
        return get_redactor(mask_dates, languages)
    if not idle.isdigit() or int(idle) < 1:
        raise ProcessingBlocked("Invalid redaction worker setting.")
    return _worker(mask_dates, tuple(languages), int(idle))
