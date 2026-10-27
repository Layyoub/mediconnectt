from decimal import Decimal

from django.db import migrations

EMERGENCY = "Appelez immédiatement les services d'urgence (112 ou le numéro d'urgence local)."

# (name, symptom, min_severity, combined_symptom, min_temperature, level, message)
RULES = [
    ("Douleur thoracique sévère", "chest_pain", 3, "", None, "urgent",
     f"Une douleur thoracique intense peut signaler un problème cardiaque grave. {EMERGENCY}"),
    ("Douleur thoracique avec essoufflement", "chest_pain", 1, "shortness_breath", None, "urgent",
     f"L'association douleur thoracique et essoufflement nécessite une prise en charge immédiate. {EMERGENCY}"),
    ("Détresse respiratoire", "shortness_breath", 3, "", None, "urgent",
     f"Une difficulté importante à respirer est une urgence. {EMERGENCY}"),
    ("Signes d'AVC", "stroke_signs", 1, "", None, "urgent",
     f"Une faiblesse d'un côté du corps ou un trouble de la parole peut être un AVC : chaque minute compte. {EMERGENCY}"),
    ("Confusion / trouble de la conscience", "confusion", 1, "", None, "urgent",
     f"Une confusion ou une somnolence anormale doit être évaluée sans délai. {EMERGENCY}"),
    ("Saignement important", "bleeding", 2, "", None, "urgent",
     f"Un saignement important nécessite une prise en charge immédiate. {EMERGENCY}"),
    ("Idées suicidaires", "suicidal_thoughts", 1, "", None, "urgent",
     "Vous n'êtes pas seul(e). Contactez immédiatement les services d'urgence ou une ligne d'écoute "
     "de prévention du suicide, et parlez-en à une personne de confiance."),
    ("Maux de tête avec raideur de nuque et fièvre", "stiff_neck", 1, "fever", None, "urgent",
     f"L'association fièvre et raideur de la nuque peut évoquer une méningite. {EMERGENCY}"),
    ("Fièvre très élevée", "", 1, "", Decimal("40.0"), "high",
     "Une fièvre à 40 °C ou plus nécessite un avis médical aujourd'hui."),
    ("Douleur abdominale sévère", "abdominal_pain", 3, "", None, "high",
     "Une douleur abdominale intense doit être évaluée par un médecin aujourd'hui."),
    ("Trouble de la vision", "vision_trouble", 2, "", None, "high",
     "Un trouble de la vision ou un œil douloureux nécessite un avis médical rapide."),
]


def seed(apps, schema_editor):
    RedFlagRule = apps.get_model("triage", "RedFlagRule")
    for name, symptom, min_sev, combined, min_temp, level, message in RULES:
        RedFlagRule.objects.get_or_create(
            name=name,
            defaults={
                "symptom": symptom,
                "min_severity": min_sev,
                "combined_symptom": combined,
                "min_temperature": min_temp,
                "level": level,
                "message": message,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("triage", "RedFlagRule").objects.filter(name__in=[r[0] for r in RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [("triage", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
