import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DEMO = "Données de démonstration, à valider par un pharmacien. "


def seed_demo(apps, schema_editor):
    Interaction = apps.get_model("records", "DrugInteraction")
    DoseLimit = apps.get_model("records", "DoseLimit")
    Contraindication = apps.get_model("records", "Contraindication")

    interactions = [
        ("warfarine", "AINS", "major", "Risque hémorragique accru ; réévaluer l'association et surveiller."),
        ("warfarine", "aspirine", "major", "Risque hémorragique accru ; vérifier l'indication et la surveillance."),
        ("IEC", "diurétiques épargneurs de potassium", "major", "Risque d'hyperkaliémie ; vérifier le bilan biologique."),
        ("clarithromycine", "simvastatine", "contraindicated", "Risque de toxicité musculaire ; association à éviter."),
        ("sildenafil", "nitrates", "contraindicated", "Risque d'hypotension sévère ; association contre-indiquée."),
        ("ISRS", "tramadol", "major", "Risque de syndrome sérotoninergique ; réévaluer l'association."),
        ("AINS", "AINS", "major", "Cumul d'AINS : risques digestif et rénal accrus."),
        ("methotrexate", "AINS", "major", "Risque de toxicité du méthotrexate ; vérifier l'indication et la surveillance."),
        ("AINS", "IEC", "major", "Risque d'altération de la fonction rénale ; surveiller selon le contexte."),
        ("AINS", "diurétiques", "moderate", "Risque rénal et possible diminution de l'effet diurétique."),
        ("benzodiazépines", "opioïdes", "major", "Risque de sédation profonde et de dépression respiratoire."),
        ("spironolactone", "potassium", "major", "Risque d'hyperkaliémie ; vérifier les traitements et le bilan."),
    ]
    for group_a, group_b, severity, advice in interactions:
        Interaction.objects.get_or_create(
            group_a=group_a,
            group_b=group_b,
            defaults={"severity": severity, "advice": DEMO + advice},
        )

    limits = [
        ("paracetamol", 3000, None, 192, None, "Adulte : limite quotidienne de démonstration."),
        ("paracetamol", 3000, 60, None, 191, "Enfant : limite pondérale quotidienne de démonstration."),
        ("ibuprofene", 1200, None, 192, None, "Adulte : repère quotidien de démonstration."),
        ("ibuprofene", 1200, 30, None, 191, "Enfant : limite pondérale quotidienne de démonstration."),
        ("diclofenac", 150, None, 192, None, "Adulte : limite quotidienne de démonstration."),
        ("naproxene", 1000, None, 192, None, "Adulte : limite quotidienne de démonstration."),
        ("metformine", 2550, None, 216, None, "Adulte : limite quotidienne de démonstration."),
        ("cetirizine", 10, None, 72, None, "Repère quotidien de démonstration pour l'adulte et l'enfant d'au moins 6 ans."),
    ]
    for dci, maximum, per_kg, min_age, max_age, note in limits:
        DoseLimit.objects.get_or_create(
            dci=dci,
            max_daily_mg=maximum,
            per_kg_daily_mg=per_kg,
            min_age_months=min_age,
            max_age_months=max_age,
            defaults={"note": DEMO + note},
        )

    contraindications = [
        ("aspirine", [], None, 191, "contraindicated", "L'aspirine est contre-indiquée avant 16 ans dans ce jeu de règles de démonstration."),
        ("codeine", [], None, 143, "contraindicated", "La codéine est contre-indiquée avant 12 ans dans ce jeu de règles de démonstration."),
        ("AINS", ["insuffisance renale"], None, None, "major", "Insuffisance rénale signalée : réévaluer l'utilisation d'un AINS."),
        ("bêta-bloquants", ["asthme"], None, None, "moderate", "Asthme signalé : vérifier le type de bêta-bloquant et le contexte clinique."),
        ("IEC", ["grossesse"], None, None, "contraindicated", "Grossesse signalée : les IEC sont à éviter ; réévaluation médicale requise."),
        ("metformine", ["insuffisance renale severe"], None, None, "contraindicated", "Insuffisance rénale sévère signalée : vérifier la contre-indication et les données cliniques."),
        ("fluoroquinolones", ["grossesse"], None, None, "major", "Grossesse signalée : réévaluer l'indication d'une fluoroquinolone."),
        ("opioïdes", ["insuffisance respiratoire"], None, None, "major", "Insuffisance respiratoire signalée : risque de dépression respiratoire à évaluer."),
    ]
    for group, keywords, min_age, max_age, severity, message in contraindications:
        Contraindication.objects.get_or_create(
            group=group,
            keywords=keywords,
            min_age_months=min_age,
            max_age_months=max_age,
            defaults={"severity": severity, "message": DEMO + message},
        )


def unseed_demo(apps, schema_editor):
    apps.get_model("records", "DrugInteraction").objects.filter(advice__startswith=DEMO).delete()
    apps.get_model("records", "DoseLimit").objects.filter(note__startswith=DEMO).delete()
    apps.get_model("records", "Contraindication").objects.filter(message__startswith=DEMO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0004_medicaldocument_appointment_alter_accesslog_action_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="accesslog",
            name="action",
            field=models.CharField(
                choices=[
                    ("view", "Consultation du dossier"),
                    ("update", "Mise à jour des informations médicales"),
                    ("note", "Ajout d'une note"),
                    ("prescription", "Rédaction d'une ordonnance"),
                    ("prescription_view", "Consultation d'une ordonnance"),
                    ("document_add", "Ajout d'un document"),
                    ("document_view", "Ouverture d'un document"),
                    ("document_delete", "Suppression d'un document"),
                    ("document_analysis", "Analyse IA d'un document"),
                    ("prescription_check", "Vérification de sécurité d'une ordonnance"),
                ],
                max_length=20,
                verbose_name="action",
            ),
        ),
        migrations.CreateModel(
            name="Contraindication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group", models.CharField(max_length=120, verbose_name="DCI ou classe")),
                ("keywords", models.JSONField(blank=True, default=list, verbose_name="mots-clés cliniques")),
                ("min_age_months", models.PositiveIntegerField(blank=True, null=True, verbose_name="âge minimum (mois)")),
                ("max_age_months", models.PositiveIntegerField(blank=True, null=True, verbose_name="âge maximum (mois)")),
                ("severity", models.CharField(choices=[("info", "Information"), ("minor", "Mineure"), ("moderate", "Modérée"), ("major", "Majeure"), ("contraindicated", "Contre-indiquée")], max_length=20, verbose_name="gravité")),
                ("message", models.TextField(verbose_name="message")),
            ],
            options={"verbose_name": "contre-indication", "verbose_name_plural": "contre-indications", "ordering": ["group"]},
        ),
        migrations.CreateModel(
            name="DoseLimit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dci", models.CharField(max_length=120, verbose_name="DCI")),
                ("max_daily_mg", models.DecimalField(blank=True, decimal_places=2, max_digits=9, null=True, verbose_name="dose maximale quotidienne (mg)")),
                ("per_kg_daily_mg", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, verbose_name="dose quotidienne (mg/kg)")),
                ("min_age_months", models.PositiveIntegerField(blank=True, null=True, verbose_name="âge minimum (mois)")),
                ("max_age_months", models.PositiveIntegerField(blank=True, null=True, verbose_name="âge maximum (mois)")),
                ("note", models.TextField(verbose_name="note")),
            ],
            options={"verbose_name": "limite de dose", "verbose_name_plural": "limites de dose", "ordering": ["dci", "min_age_months", "max_age_months"]},
        ),
        migrations.CreateModel(
            name="DrugInteraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group_a", models.CharField(max_length=120, verbose_name="DCI ou classe A")),
                ("group_b", models.CharField(max_length=120, verbose_name="DCI ou classe B")),
                ("severity", models.CharField(choices=[("info", "Information"), ("minor", "Mineure"), ("moderate", "Modérée"), ("major", "Majeure"), ("contraindicated", "Contre-indiquée")], max_length=20, verbose_name="gravité")),
                ("advice", models.TextField(verbose_name="conduite à tenir")),
            ],
            options={"verbose_name": "interaction médicamenteuse", "verbose_name_plural": "interactions médicamenteuses", "ordering": ["group_a", "group_b"]},
        ),
        migrations.CreateModel(
            name="PrescriptionCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("findings", models.JSONField(blank=True, default=list, verbose_name="alertes de règles")),
                ("checked_items", models.JSONField(blank=True, default=list, verbose_name="médicaments vérifiés")),
                ("max_severity", models.CharField(choices=[("info", "Information"), ("minor", "Mineure"), ("moderate", "Modérée"), ("major", "Majeure"), ("contraindicated", "Contre-indiquée")], default="info", max_length=20, verbose_name="gravité maximale")),
                ("override_reason", models.TextField(blank=True, verbose_name="motif de dérogation")),
                ("ai_opinion", models.JSONField(blank=True, default=dict, verbose_name="avis IA")),
                ("ai_status", models.CharField(choices=[("idle", "Non demandée"), ("running", "En cours"), ("done", "Terminée"), ("error", "Échec")], default="idle", max_length=10, verbose_name="état de l'avis IA")),
                ("ai_error", models.CharField(blank=True, max_length=300, verbose_name="erreur IA")),
                ("ai_model", models.CharField(blank=True, max_length=60, verbose_name="modèle IA")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="créé le")),
                ("doctor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="prescription_checks", to=settings.AUTH_USER_MODEL, verbose_name="médecin")),
                ("prescription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="safety_checks", to="records.prescription", verbose_name="ordonnance")),
                ("record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="prescription_checks", to="records.medicalrecord", verbose_name="dossier")),
            ],
            options={"verbose_name": "vérification de prescription", "verbose_name_plural": "vérifications de prescription", "ordering": ["-created_at"]},
        ),
        migrations.RunPython(seed_demo, unseed_demo),
    ]
