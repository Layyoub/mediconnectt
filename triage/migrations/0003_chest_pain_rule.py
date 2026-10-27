from django.db import migrations

NAME = "Douleur thoracique"


def add_rule(apps, schema_editor):
    apps.get_model("triage", "RedFlagRule").objects.get_or_create(
        name=NAME,
        defaults={
            "symptom": "chest_pain",
            "min_severity": 1,
            "level": "high",
            "message": "Toute douleur thoracique doit être évaluée par un médecin aujourd'hui, "
                       "même si elle semble légère.",
        },
    )


def remove_rule(apps, schema_editor):
    apps.get_model("triage", "RedFlagRule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [("triage", "0002_seed_red_flags")]

    operations = [migrations.RunPython(add_rule, remove_rule)]
