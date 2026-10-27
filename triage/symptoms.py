"""Symptom catalog used by the triage questionnaire and the scoring engine.

Each symptom has:
- weight: how concerning it is on its own (1 = minor, 10 = critical)
- specialty: the DoctorProfile specialty best suited to it
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Symptom:
    code: str
    label: str
    category: str
    icon: str
    weight: int
    specialty: str


CATEGORIES = [
    ("general", "Signes généraux", "thermometer-half"),
    ("respiratory", "Respiratoire & ORL", "lungs"),
    ("cardio", "Cœur & circulation", "heart-pulse"),
    ("digestive", "Digestif", "capsule"),
    ("neuro", "Neurologique", "activity"),
    ("skin", "Peau & yeux", "eye"),
    ("mental", "Santé mentale", "emoji-neutral"),
    ("other", "Urinaire & gynécologique", "droplet"),
]

SYMPTOMS = [
    # Signes généraux
    Symptom("fever", "Fièvre", "general", "thermometer-high", 3, "general"),
    Symptom("fatigue", "Fatigue inhabituelle", "general", "battery-half", 2, "general"),
    Symptom("body_aches", "Courbatures", "general", "person-arms-up", 1, "general"),
    # Respiratoire & ORL
    Symptom("cough", "Toux", "respiratory", "lungs", 2, "pulmonology"),
    Symptom("shortness_breath", "Essoufflement / difficulté à respirer", "respiratory", "wind", 6, "pulmonology"),
    Symptom("sore_throat", "Mal de gorge", "respiratory", "chat-left", 1, "ent"),
    Symptom("runny_nose", "Nez qui coule / bouché", "respiratory", "droplet-half", 1, "ent"),
    Symptom("ear_pain", "Douleur à l'oreille", "respiratory", "ear", 1, "ent"),
    # Cœur & circulation
    Symptom("chest_pain", "Douleur ou oppression thoracique", "cardio", "heart-pulse", 8, "cardiology"),
    Symptom("palpitations", "Palpitations", "cardio", "heart", 4, "cardiology"),
    Symptom("leg_swelling", "Jambes gonflées", "cardio", "arrows-expand", 3, "cardiology"),
    # Digestif
    Symptom("abdominal_pain", "Douleur abdominale", "digestive", "bullseye", 4, "general"),
    Symptom("nausea", "Nausées / vomissements", "digestive", "emoji-dizzy", 2, "general"),
    Symptom("diarrhea", "Diarrhée", "digestive", "water", 2, "general"),
    Symptom("bleeding", "Saignement important (vomi, selles, plaie)", "digestive", "droplet-fill", 9, "general"),
    # Neurologique
    Symptom("headache", "Maux de tête", "neuro", "lightning", 2, "general"),
    Symptom("stiff_neck", "Raideur de la nuque", "neuro", "person", 5, "general"),
    Symptom("dizziness", "Vertiges", "neuro", "arrow-repeat", 3, "general"),
    Symptom("confusion", "Confusion / somnolence anormale", "neuro", "question-circle", 9, "general"),
    Symptom("stroke_signs", "Faiblesse d'un côté / trouble de la parole", "neuro", "person-slash", 10, "general"),
    # Peau & yeux
    Symptom("rash", "Éruption cutanée / boutons", "skin", "bandaid", 2, "dermatology"),
    Symptom("itching", "Démangeaisons", "skin", "hand-index", 1, "dermatology"),
    Symptom("vision_trouble", "Trouble de la vision / œil douloureux", "skin", "eye", 5, "ophthalmology"),
    # Santé mentale
    Symptom("anxiety", "Anxiété / stress important", "mental", "cloud-drizzle", 2, "psychiatry"),
    Symptom("low_mood", "Tristesse persistante / perte d'intérêt", "mental", "cloud", 3, "psychiatry"),
    Symptom("suicidal_thoughts", "Idées suicidaires", "mental", "exclamation-octagon", 10, "psychiatry"),
    # Urinaire & gynécologique
    Symptom("urinary_pain", "Brûlures en urinant", "other", "droplet", 2, "general"),
    Symptom("pelvic_pain", "Douleur pelvienne / gynécologique", "other", "gender-female", 3, "gynecology"),
]

SYMPTOMS_BY_CODE = {s.code: s for s in SYMPTOMS}
SYMPTOM_CHOICES = [(s.code, s.label) for s in SYMPTOMS]


def grouped_symptoms():
    """[(category_code, label, icon, [Symptom, ...]), ...] in display order."""
    return [
        (code, label, icon, [s for s in SYMPTOMS if s.category == code])
        for code, label, icon in CATEGORIES
    ]
