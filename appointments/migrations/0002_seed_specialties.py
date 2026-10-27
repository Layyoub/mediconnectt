from django.db import migrations

SPECIALTIES = [
    ("general", "Médecine générale", "heart-pulse", "Premier recours : fièvre, infections, suivi et orientation."),
    ("cardiology", "Cardiologie", "activity", "Cœur et vaisseaux : douleur thoracique, palpitations, tension."),
    ("dermatology", "Dermatologie", "bandaid", "Peau, cheveux et ongles : éruptions, acné, grains de beauté."),
    ("pediatrics", "Pédiatrie", "balloon-heart", "Santé des enfants et des adolescents."),
    ("gynecology", "Gynécologie", "gender-female", "Santé de la femme, contraception et suivi de grossesse."),
    ("psychiatry", "Psychiatrie", "chat-heart", "Anxiété, humeur, sommeil et santé mentale."),
    ("ent", "ORL", "ear", "Oreilles, nez et gorge : sinusite, angine, vertiges."),
    ("ophthalmology", "Ophtalmologie", "eye", "Yeux et vision : rougeur, gêne visuelle."),
    ("pulmonology", "Pneumologie", "lungs", "Poumons et respiration : toux persistante, asthme."),
    ("other", "Autre spécialité", "hospital", "Autres spécialités médicales."),
]


def seed(apps, schema_editor):
    Specialty = apps.get_model("appointments", "Specialty")
    for code, name, icon, description in SPECIALTIES:
        Specialty.objects.update_or_create(
            code=code, defaults={"name": name, "icon": icon, "description": description}
        )


def unseed(apps, schema_editor):
    apps.get_model("appointments", "Specialty").objects.filter(code__in=[s[0] for s in SPECIALTIES]).delete()


class Migration(migrations.Migration):
    dependencies = [("appointments", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
