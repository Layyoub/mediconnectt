"""Dependency-free normalization and medication-name matching."""

import difflib
import re
import unicodedata


ALIASES = {
    "paracetamol": ("paracetamol", "analgesique"),
    "acetaminophen": ("paracetamol", "analgesique"),
    "doliprane": ("paracetamol", "analgesique"),
    "efferalgan": ("paracetamol", "analgesique"),
    "dafalgan": ("paracetamol", "analgesique"),
    "clamoxyl": ("amoxicilline", "penicillines"),
    "amoxicillin": ("amoxicilline", "penicillines"),
    "augmentin": ("amoxicilline acide clavulanique", "penicillines"),
    "voltarene": ("diclofenac", "ains"),
    "brufen": ("ibuprofene", "ains"),
    "advil": ("ibuprofene", "ains"),
    "kardegic": ("aspirine", "antiagregants"),
    "glucophage": ("metformine", "biguanides"),
    "warfarine": ("warfarine", "anticoagulants"),
    "warfarin": ("warfarine", "anticoagulants"),
    "aspirine": ("aspirine", "antiagregants"),
    "aspirin": ("aspirine", "antiagregants"),
    "acide acetylsalicylique": ("aspirine", "antiagregants"),
    "clarithromycine": ("clarithromycine", "macrolides"),
    "clarithromycin": ("clarithromycine", "macrolides"),
    "simvastatine": ("simvastatine", "statines"),
    "simvastatin": ("simvastatine", "statines"),
    "sildenafil": ("sildenafil", "inhibiteurs phosphodiesterase 5"),
    "tramadol": ("tramadol", "opioides"),
    "methotrexate": ("methotrexate", "antimetabolites"),
    "metotrexate": ("methotrexate", "antimetabolites"),
    "spironolactone": ("spironolactone", "diuretiques epargneurs de potassium"),
    "enalapril": ("enalapril", "iec"),
    "lisinopril": ("lisinopril", "iec"),
    "ibuprofene": ("ibuprofene", "ains"),
    "ibuprofen": ("ibuprofene", "ains"),
    "diclofenac": ("diclofenac", "ains"),
    "codeine": ("codeine", "opioides"),
    "beta bloquant": ("beta bloquant", "beta bloquants"),
    "nitrates": ("nitrates", "nitrates"),
    "naproxene": ("naproxene", "ains"),
    "naproxen": ("naproxene", "ains"),
    "metoprolol": ("metoprolol", "beta bloquants"),
    "propranolol": ("propranolol", "beta bloquants"),
    "atenolol": ("atenolol", "beta bloquants"),
    "bisoprolol": ("bisoprolol", "beta bloquants"),
    "ramipril": ("ramipril", "iec"),
    "perindopril": ("perindopril", "iec"),
    "nitroglycerine": ("nitroglycerine", "nitrates"),
    "trinitrine": ("nitroglycerine", "nitrates"),
    "isosorbide": ("isosorbide", "nitrates"),
    "fluoxetine": ("fluoxetine", "isrs"),
    "sertraline": ("sertraline", "isrs"),
    "citalopram": ("citalopram", "isrs"),
    "escitalopram": ("escitalopram", "isrs"),
    "paroxetine": ("paroxetine", "isrs"),
    "fluvoxamine": ("fluvoxamine", "isrs"),
    "diazepam": ("diazepam", "benzodiazepines"),
    "lorazepam": ("lorazepam", "benzodiazepines"),
    "alprazolam": ("alprazolam", "benzodiazepines"),
    "clonazepam": ("clonazepam", "benzodiazepines"),
    "morphine": ("morphine", "opioides"),
    "oxycodone": ("oxycodone", "opioides"),
    "fentanyl": ("fentanyl", "opioides"),
    "ciprofloxacine": ("ciprofloxacine", "fluoroquinolones"),
    "levofloxacine": ("levofloxacine", "fluoroquinolones"),
}
CLASS_NAMES = {
    "ains": ("ains", "ains"),
    "anti inflammatoires non steroidiens": ("ains", "ains"),
    "iec": ("iec", "iec"),
    "beta bloquant": ("beta bloquant", "beta bloquants"),
    "beta bloquants": ("beta bloquants", "beta bloquants"),
    "diuretique epargneur de potassium": ("diuretique epargneur de potassium", "diuretiques epargneurs de potassium"),
    "cephalosporine": ("cephalosporines", "cephalosporines"),
    "cephalosporines": ("cephalosporines", "cephalosporines"),
    "penicilline": ("penicillines", "penicillines"),
    "penicillines": ("penicillines", "penicillines"),
    "nitrates": ("nitrates", "nitrates"),
    "isrs": ("isrs", "isrs"),
    "anticoagulant": ("anticoagulants", "anticoagulants"),
    "anticoagulants": ("anticoagulants", "anticoagulants"),
    "insuline": ("insuline", "antidiabetiques"),
    "aspirine": ("aspirine", "antiagregants"),
    "diuretique": ("diuretiques", "diuretiques"),
    "diuretiques": ("diuretiques", "diuretiques"),
    "diuretique epargneur de potassium": (
        "diuretiques epargneurs de potassium", "diuretiques epargneurs de potassium"
    ),
    "diuretiques epargneurs de potassium": (
        "diuretiques epargneurs de potassium", "diuretiques epargneurs de potassium"
    ),
    "potassium": ("potassium", "potassium"),
    "opioide": ("opioides", "opioides"),
    "opioides": ("opioides", "opioides"),
    "benzodiazepine": ("benzodiazepines", "benzodiazepines"),
    "benzodiazepines": ("benzodiazepines", "benzodiazepines"),
    "fluoroquinolone": ("fluoroquinolones", "fluoroquinolones"),
    "fluoroquinolones": ("fluoroquinolones", "fluoroquinolones"),
    "statine": ("statines", "statines"),
    "statines": ("statines", "statines"),
}

_STRENGTH = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mcg|µg|ug|mg|g|ml|%|ui|iu)\b", re.IGNORECASE
)
_FORM_WORDS = re.compile(
    r"\b(?:comprime(?:s)?|gelule(?:s)?|capsule(?:s)?|sirop|sachet(?:s)?|"
    r"goutte(?:s)?|spray|creme|pommade|collyre|injectable|solution|"
    r"suppositoire|inhalateur|buvable|oral|orale|voie)\b"
)


def normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _STRENGTH.sub(" ", text)
    text = _FORM_WORDS.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _field(entry, name, default=""):
    if isinstance(entry, dict):
        return entry.get(name, default) or default
    return getattr(entry, name, default) or default


def resolve_drug(text, linked_drug=None, catalog=()):
    """Resolve to a DCI/class using a linked catalogue entry, exact names, aliases, then fuzzy match."""
    typed = normalize_text(text)
    if not typed:
        return None

    linked_dci = _field(linked_drug, "dci")
    linked_class = _field(linked_drug, "drug_class")
    if linked_dci or linked_class:
        return {
            "name": str(text),
            "dci": linked_dci or _field(linked_drug, "name"),
            "drug_class": linked_class,
            "strength": _field(linked_drug, "strength"),
            "matched": True,
            "source": "linked",
        }

    entries = list(catalog)
    candidates = []
    for entry in entries:
        name = _field(entry, "name")
        dci = _field(entry, "dci")
        drug_class = _field(entry, "drug_class")
        strength = _field(entry, "strength")
        candidates.append((entry, name, dci, drug_class, strength))
        if typed in {normalize_text(name), normalize_text(dci)}:
            return _resolved(text, name, dci, drug_class, strength, "exact")

    if typed in ALIASES:
        dci, drug_class = ALIASES[typed]
        return _resolved(text, dci, dci, drug_class, "", "alias")
    if typed in CLASS_NAMES:
        dci, drug_class = CLASS_NAMES[typed]
        return _resolved(text, dci, drug_class, "", "", "class")

    names = {normalize_text(value): (entry, name, dci, drug_class, strength)
             for entry, name, dci, drug_class, strength in candidates
             for value in (name, dci) if normalize_text(value)}
    match = difflib.get_close_matches(typed, names.keys(), n=1, cutoff=0.9)
    if match:
        entry, name, dci, drug_class, strength = names[match[0]]
        return _resolved(text, name, dci, drug_class, strength, "fuzzy")
    return None


def _resolved(original, name, dci, drug_class, strength, source):
    return {
        "name": str(original),
        "dci": dci or name,
        "drug_class": drug_class,
        "strength": strength,
        "matched": True,
        "source": source,
    }
