import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0005_pharmacheck"),
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
                    ("rx_instructions", "Consultation des consignes de prescription"),
                ],
                max_length=20,
                verbose_name="action",
            ),
        ),
        migrations.AlterField(
            model_name="prescriptioncheck",
            name="doctor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="prescription_checks",
                to=settings.AUTH_USER_MODEL,
                verbose_name="médecin",
            ),
        ),
        migrations.AddField(
            model_name="prescriptioncheck",
            name="confirmed_resolutions",
            field=models.JSONField(blank=True, default=list, verbose_name="médicaments reconnus et confirmés"),
        ),
        migrations.AddField(
            model_name="prescriptioncheck",
            name="ai_cache_key",
            field=models.CharField(blank=True, max_length=64, verbose_name="clé de cache IA"),
        ),
        migrations.CreateModel(
            name="PatientInstructions",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text_fr", models.TextField(blank=True, verbose_name="consignes en français")),
                ("text_ar", models.TextField(blank=True, verbose_name="consignes en arabe")),
                ("status", models.CharField(choices=[("draft", "Brouillon"), ("approved", "Approuvées")], default="draft", max_length=10, verbose_name="validation")),
                ("generation_status", models.CharField(choices=[("running", "En cours"), ("done", "Terminée"), ("error", "Échec")], default="running", max_length=10, verbose_name="génération IA")),
                ("generation_error", models.CharField(blank=True, max_length=300, verbose_name="erreur de génération")),
                ("approved_at", models.DateTimeField(blank=True, null=True, verbose_name="validées le")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="créées le")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="modifiées le")),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_patient_instructions", to=settings.AUTH_USER_MODEL, verbose_name="validées par")),
                ("prescription", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="patient_instructions", to="records.prescription", verbose_name="ordonnance")),
            ],
            options={
                "verbose_name": "consignes patient de prescription",
                "verbose_name_plural": "consignes patient de prescription",
            },
        ),
    ]
