# Démonstration Navigation mains libres (2 minutes)

1. Sur la page d'accueil, ouvrez **Accessibilité**, choisissez **Yeux + mains (recommandé)** et acceptez l'avis caméra. Le visage et la main sont suivis localement par MediaPipe ; aucune image n'est envoyée au serveur.
2. Regardez un lien ou bouton : le curseur suit le milieu des iris. Clignez pour cliquer. Pour adapter la détection des clignements à votre caméra, choisissez **Calibrer le clignement** : gardez les yeux ouverts 3 secondes, puis clignez naturellement pendant 6 secondes.
3. Faites un pincement pouce-index sur une cible pour cliquer ; gardez le pincement et déplacez la main verticalement pour faire défiler. Le suivi des yeux et des mains partage le même flux caméra.
4. Regardez le bouton d'accessibilité pour l'ouvrir, puis utilisez **Pause**/**Reprendre** et **Arrêter** dans le panneau flottant. `Alt+Maj+G` active ou arrête le dernier mode choisi.
5. Dans une téléconsultation de démonstration, activez mains libres et ouvrez un compte rendu de laboratoire. Vérifiez que le suivi continue indépendamment de la caméra envoyée au correspondant ; le flux mains libres est un flux séparé, local et basse résolution.

## Limites connues

- Une caméra, un navigateur compatible, un contexte HTTPS (ou localhost) et l'autorisation caméra sont nécessaires. Les performances et la précision varient selon l'éclairage, la caméra et l'appareil.
- La position du curseur suit les iris par coordonnées normalisées MediaPipe ; la précision dépend toujours de l'éclairage, de la caméra et de l'angle du visage.
- Le navigateur bloque les clics synthétiques qui ouvriraient un sélecteur de fichier ou une demande d'autorisation. Ces actions exigent un vrai clic.
- Les données visuelles sont traitées dans le navigateur. Les scripts et le modèle de vision sont téléchargés depuis jsDelivr et Google Storage uniquement après activation d'un mode correspondant.
- Les tests automatisés valident la logique et l'intégration HTML, pas le fonctionnement d'une caméra réelle ni les mouvements du pointeur.
