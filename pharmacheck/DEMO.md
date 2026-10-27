# Démonstration Pharma-Check

> Les interactions, limites et contre-indications préchargées sont des données de démonstration à valider par un pharmacien. Pharma-Check est une aide à la décision, pas une prescription ni un substitut au jugement clinique.

1. Préparez la base et les comptes :

   ```sh
   python manage.py migrate
   python manage.py seed_pharmacheck_demo
   ```

   Connectez-vous comme `pharmacheck@mediconnect.demo` avec le mot de passe de démonstration `MediConnect2026!`. Les comptes et données créés par cette commande sont fictifs.

2. Ouvrez le dossier **Patient Démo A** (traitement en cours : warfarine), créez une ordonnance et saisissez `ibuprofène`. La règle d'interaction avec la warfarine doit afficher immédiatement une alerte rouge majeure. Après l'arrêt de la saisie, **Pharmacien IA** effectue sa seconde lecture et peut proposer une alternative non posologique à discuter.

3. Dans une ligne de médicament, tapez `voltaren` (variante volontairement non reconnue par le matcher local). L'IA peut proposer une correspondance diclofénac avec son niveau de confiance et ses éléments d'appui. Confirmez ou rejetez la proposition. Après confirmation, les règles locales réévaluent l'ordonnance comme un AINS ; sans confirmation, le nom reste non résolu.

4. Ouvrez **Patient Démo B** (allergie déclarée à la pénicilline) et saisissez `amoxicilline`. La règle d'allergie doit afficher une alerte rouge contre-indiquée.

5. Ouvrez **Patient Démo C** (8 ans, 25 kg) et saisissez du paracétamol à `1000 mg`, `2 fois par jour`. Le total dépasse le repère pédiatrique de démonstration de 60 mg/kg/jour et doit déclencher une alerte de dose rouge.

6. Pour poursuivre une prescription malgré une alerte majeure ou contre-indiquée, saisissez un motif d'au moins 10 caractères, puis enregistrez. Le serveur recalcule les règles avant de sauvegarder. Après l'enregistrement, l'IA peut générer un brouillon d'instructions en français et en arabe à partir des seules lignes sauvegardées. Le médecin les relit, les modifie si nécessaire, puis choisit **Approuver et publier au patient**. Le patient ne voit les instructions qu'après cette approbation et peut basculer entre Français et العربية.

Sans clé OpenAI ou si un appel IA échoue, le panneau indique que l'avis IA est indisponible ; les vérifications et blocages déterministes restent actifs.
