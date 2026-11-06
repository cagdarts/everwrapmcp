# Choose your note languages

EverWrapMCP provides optional English, Turkish, Spanish, French and German packs.
Choose the languages in your notes, including mixed notes, during
[installation](INSTALL.md#choose-language-packs). Only selected models are installed
and loaded; changing a selection does not delete previously downloaded files.
Existing policies without a language field still default to English and Turkish.

The installer runs a fictional name-masking check before saving the selection.
The block list remains the first access check. Language settings stay in the private
local policy and cannot be overridden by a note-read or search argument.

## Models and licenses

| Language | Local model | Model license |
| --- | --- | --- |
| English | en_core_web_md 3.8.0 | MIT |
| Turkish | akdeniz27/bert-base-turkish-cased-ner, pinned revision | MIT |
| Spanish | es_core_news_md 3.8.0 | GNU GPL 3.0 |
| French | fr_core_news_md 3.8.0 | LGPL-LR |
| German | de_core_news_md 3.8.0 | MIT |

The new medium spaCy models download roughly 40–44 MiB each. Installation fetches
model packages from official releases; inference runs locally. Runtime does not
download missing models. Medium models limit installation size; they are still
statistical detectors, not complete privacy filters.

EverWrapMCP's MIT license covers its own code. Models are separate third-party
artifacts with their own licenses; the installer displays each selected model's
license. Read upstream terms when distributing model bundles or derivatives.

Official model references: [English](https://spacy.io/models/en),
[Spanish](https://spacy.io/models/es), [French](https://spacy.io/models/fr),
[German](https://spacy.io/models/de),
[Turkish](https://huggingface.co/akdeniz27/bert-base-turkish-cased-ner).

## What coverage means

Language detection chooses a selected local named-entity model for each text unit.
Presidio merges those detections with shared email, credential and checksum rules.
Additional label patterns cover names, addresses, phones, passwords and birthdays
in the five languages. The user's date-masking preference still applies.

Passages identified as unsupported or uncertain are withheld. This cannot guarantee
that an unsupported language is always recognized: short phrases and language
switches inside one sentence can be misclassified. News-trained models can miss
journal names and mask ordinary words. Unlabelled addresses, regional phone formats,
local national identifiers, and obfuscated values are not comprehensively covered.
Synthetic regression coverage is not an independent privacy certification.

## Download and scan warning

**Model wheels are installable third-party packages.** Use the pinned official
release sources; do not substitute an arbitrary download link. The advisory scan
skipped all four spaCy model wheels because it could not resolve their direct URLs
to advisory package versions. They were **not assessed**, and their weights were
not security-certified. Passing masking tests checks behavior, not package safety.
[Audit scope and report](DEPENDENCY_SECURITY.md#five-language-pack-update).

## Validation of this release

The current application suite passed **295 tests**, excluding the separate
historical stock-baseline gate. The 19 new tests exercise the three new models,
ordinary-text readability, names, organizations, places, labelled contact details,
passwords, birthdays, date preferences, mixed five-language paragraphs, pack
isolation, and block-list preservation. Fixtures are fictional.

A fresh temporary Python environment installed the core plus Spanish/French/German
through the actual installer, passed its fictional masking checks and saved its
synthetic policy. English model, Transformers and torch were absent. This is a
local installation check, not live Evernote/client validation in those languages.
The German model masked an ordinary word in the setup example; false positives
remain visible rather than hidden by weakening name detection.
