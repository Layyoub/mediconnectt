import django.db.models.deletion
from django.db import migrations, models

FORMS = {
    "tablet": "comprimé", "capsule": "gélule", "syrup": "sirop", "sachet": "sachet", "drops": "gouttes",
    "spray": "spray", "inhaler": "inhalateur", "cream": "crème / pommade", "injection": "injectable",
    "suppository": "suppositoire", "other": "autre",
}


def copy_names(apps, schema_editor):
    """Existing lines keep the catalogue drug they were written with, as text."""
    Item = apps.get_model("records", "PrescriptionItem")
    for item in Item.objects.select_related("drug"):
        d = item.drug
        item.drug_name = f"{d.name} {d.strength}".strip() + f" ({FORMS.get(d.form, d.form)})"
        item.save(update_fields=["drug_name"])


class Migration(migrations.Migration):
    dependencies = [("records", "0002_seed_drugs")]

    operations = [
        migrations.AddField(
            model_name="prescriptionitem",
            name="drug_name",
            field=models.CharField(default="", max_length=150, verbose_name="médicament"),
            preserve_default=False,
        ),
        migrations.RunPython(copy_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="prescriptionitem",
            name="drug",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
                to="records.drug", verbose_name="fiche du catalogue",
            ),
        ),
    ]
