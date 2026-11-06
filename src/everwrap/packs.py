"""Static local pack metadata; importing this module loads no models."""

PACKS = {
    "en": {"name": "English", "packages": ["https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl"],
           "sample": "Talk to Alex Smith about the community garden.", "private": ["Alex", "Smith"]},
    "tr": {"name": "Turkish", "packages": ["transformers==5.17.0", "torch==2.14.0", "safetensors==0.8.0"],
           "sample": "Bugün Ayşe Yılmaz ile konuştum.", "private": ["Ayşe", "Yılmaz"]},
    "es": {"name": "Spanish", "model": "es_core_news_md", "license": "GNU GPL 3.0",
           "sample": "Ayer hablé con María García sobre el jardín comunitario.", "private": ["María", "García"]},
    "fr": {"name": "French", "model": "fr_core_news_md", "license": "LGPL-LR",
           "sample": "Hier, j'ai parlé avec Marie Dupont du jardin collectif.", "private": ["Marie", "Dupont"]},
    "de": {"name": "German", "model": "de_core_news_md", "license": "MIT",
           "sample": "Gestern habe ich mit Anna Müller über den Gemeinschaftsgarten gesprochen.", "private": ["Anna", "Müller"]},
}
PACKS["en"].update(model="en_core_web_md", license="MIT")
PACKS["tr"]["license"] = "MIT"
for code in ("es", "fr", "de"):
    model = PACKS[code]["model"]
    PACKS[code]["packages"] = [f"https://github.com/explosion/spacy-models/releases/download/{model}-3.8.0/{model}-3.8.0-py3-none-any.whl"]

SUPPORTED_LANGUAGES = frozenset(PACKS)


def validate_languages(values):
    if (not values or any(type(x) is not str or x not in PACKS for x in values)
            or len(set(values)) != len(values)):
        raise ValueError("Choose one or more supported language packs: en, tr, es, fr, de.")
    return tuple(sorted(values))
