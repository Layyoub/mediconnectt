from django.db import migrations

# (name, DCI, class, form, strength)
DRUGS = [
    ("Paracétamol", "paracétamol", "antalgiques", "tablet", "500 mg"),
    ("Paracétamol", "paracétamol", "antalgiques", "tablet", "1 g"),
    ("Paracétamol", "paracétamol", "antalgiques", "syrup", "2,4 %"),
    ("Ibuprofène", "ibuprofène", "AINS", "tablet", "200 mg"),
    ("Ibuprofène", "ibuprofène", "AINS", "tablet", "400 mg"),
    ("Kétoprofène", "kétoprofène", "AINS", "capsule", "100 mg"),
    ("Aspirine", "acide acétylsalicylique", "AINS", "sachet", "500 mg"),
    ("Amoxicilline", "amoxicilline", "pénicillines", "capsule", "500 mg"),
    ("Amoxicilline", "amoxicilline", "pénicillines", "tablet", "1 g"),
    ("Amoxicilline / acide clavulanique", "amoxicilline, acide clavulanique", "pénicillines", "tablet", "1 g / 125 mg"),
    ("Azithromycine", "azithromycine", "macrolides", "tablet", "250 mg"),
    ("Clarithromycine", "clarithromycine", "macrolides", "tablet", "500 mg"),
    ("Céfuroxime", "céfuroxime", "céphalosporines", "tablet", "500 mg"),
    ("Ciprofloxacine", "ciprofloxacine", "fluoroquinolones", "tablet", "500 mg"),
    ("Fosfomycine", "fosfomycine trométamol", "antibiotiques urinaires", "sachet", "3 g"),
    ("Métronidazole", "métronidazole", "nitro-imidazolés", "tablet", "500 mg"),
    ("Oméprazole", "oméprazole", "inhibiteurs de la pompe à protons", "capsule", "20 mg"),
    ("Ésoméprazole", "ésoméprazole", "inhibiteurs de la pompe à protons", "tablet", "40 mg"),
    ("Phloroglucinol", "phloroglucinol", "antispasmodiques", "tablet", "80 mg"),
    ("Dompéridone", "dompéridone", "antiémétiques", "tablet", "10 mg"),
    ("Lopéramide", "lopéramide", "antidiarrhéiques", "capsule", "2 mg"),
    ("Solution de réhydratation orale", "électrolytes", "réhydratation", "sachet", ""),
    ("Cétirizine", "cétirizine", "antihistaminiques", "tablet", "10 mg"),
    ("Desloratadine", "desloratadine", "antihistaminiques", "tablet", "5 mg"),
    ("Salbutamol", "salbutamol", "bronchodilatateurs", "inhaler", "100 µg/dose"),
    ("Budésonide", "budésonide", "corticoïdes inhalés", "inhaler", "200 µg/dose"),
    ("Prednisolone", "prednisolone", "corticoïdes", "tablet", "20 mg"),
    ("Sérum physiologique", "chlorure de sodium", "lavage nasal", "drops", "0,9 %"),
    ("Spray nasal à l'eau de mer", "eau de mer", "lavage nasal", "spray", ""),
    ("Amlodipine", "amlodipine", "inhibiteurs calciques", "tablet", "5 mg"),
    ("Ramipril", "ramipril", "IEC", "tablet", "5 mg"),
    ("Bisoprolol", "bisoprolol", "bêtabloquants", "tablet", "5 mg"),
    ("Atorvastatine", "atorvastatine", "statines", "tablet", "20 mg"),
    ("Metformine", "metformine", "antidiabétiques", "tablet", "850 mg"),
    ("Lévothyroxine", "lévothyroxine", "hormones thyroïdiennes", "tablet", "50 µg"),
    ("Sertraline", "sertraline", "antidépresseurs ISRS", "capsule", "50 mg"),
    ("Hydrocortisone crème", "hydrocortisone", "dermocorticoïdes", "cream", "1 %"),
    ("Crème émolliente", "glycérol, vaseline, paraffine", "émollients", "cream", ""),
    ("Fusidate de sodium", "acide fusidique", "antibiotiques locaux", "cream", "2 %"),
    ("Collyre antiseptique", "céthexonium", "antiseptiques oculaires", "drops", ""),
    ("Fer", "sulfate ferreux", "antianémiques", "tablet", "80 mg"),
    ("Vitamine D3", "cholécalciférol", "vitamines", "drops", "10 000 UI/ml"),
    ("Acide folique", "acide folique", "vitamines", "tablet", "5 mg"),
]


def seed(apps, schema_editor):
    Drug = apps.get_model("records", "Drug")
    for name, dci, drug_class, form, strength in DRUGS:
        Drug.objects.get_or_create(
            name=name, form=form, strength=strength, defaults={"dci": dci, "drug_class": drug_class}
        )


def unseed(apps, schema_editor):
    Drug = apps.get_model("records", "Drug")
    for name, _, _, form, strength in DRUGS:
        Drug.objects.filter(name=name, form=form, strength=strength).delete()


class Migration(migrations.Migration):
    dependencies = [("records", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
