# Clip Usine

Fabrique des shorts verticaux sous-titrés à partir de clips et de VOD, sans rien installer :
tout tourne dans GitHub Actions (gratuit sur un dépôt public), et se pilote depuis un navigateur.

Deux pièces :

| Pièce | Rôle |
|---|---|
| **Fabriquer des clips** (`clip.yml`) | Lien de clip ou de VOD → MP4 1080×1920, sous-titres mot à mot, titre incrusté, mention pub, fiche de publication avec prompt Claude |
| **Radar Twitch** (`radar.yml` + page web) | Toutes les heures : qui streame le jeu en français, quels clips montent, quelles chaînes démarcher |

## Mise en route (10 minutes, tout dans le navigateur)

1. **Pages** : Settings → Pages → Source « Deploy from a branch » → branche `main`, dossier `/docs`.
   La page est en ligne sur `https://TON-PSEUDO.github.io/clip-usine/`.
2. **Clés Twitch** (pour le radar) : sur <https://dev.twitch.tv/console> → « Register Your Application »
   (nom libre, URL de redirection `http://localhost`, catégorie « Analytics Tool », type « Confidential »).
   Copie le *Client ID*, génère un *Client Secret*.
3. Dans le dépôt : Settings → Secrets and variables → Actions → *New repository secret* :
   `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET`.
4. Onglet **Actions** → « Radar Twitch » → *Run workflow*. La page se remplit en une minute.

## Fabriquer un clip

Pour vérifier que tout marche sans vraie vidéo : mets `test` dans le champ Lien.

Onglet **Actions** → « Fabriquer des clips » → *Run workflow*, puis remplis le formulaire.

| Champ | Exemple | Effet |
|---|---|---|
| Lien | `https://clips.twitch.tv/...` | Un ou plusieurs liens, séparés par un espace |
| Titre | `Il rate TOUT le braquage` | Bandeau en haut, toute la durée |
| Début / Fin | `1:02:30` / `1:03:10` | Découpe une VOD. Vide = clip entier (60 s max) |
| Auto | `5` | VOD longue : trouve seul les 5 moments les plus intenses |
| Mise en page | `flou`, `zoom`, `cam` | Fond flouté, plein écran, ou facecam en haut + jeu en bas |
| Mention | `Partenariat rémunéré` | Affichée 4 s au début (souvent exigée par les campagnes) |

Quand le job est vert, ouvre-le : les clips sont dans **Artifacts** (un zip avec les MP4,
les sous-titres `.srt` et une fiche `.md` par clip). Garde-les 7 jours max : télécharge-les.

Le mode **auto** repère les pics sonores (rires, cris, action) et démarre 12 secondes avant.
C'est un tri, pas un monteur : regarde chaque clip avant de le poster.

## Règles à ne pas enfreindre

- Ne clippe que ce qu'on t'autorise : le brief d'une campagne qui cite la source, ou l'accord écrit du streamer.
  Une VOD publique n'est pas une autorisation.
- Rockstar fait retirer les spoilers (fin du jeu, cinématiques isolées, montages de scènes d'histoire)
  et les images d'avant la sortie. Clippe le joueur, pas l'intrigue.
- Clip rémunéré = mention de partenariat visible, et case « contenu de marque » cochée dans l'appli.
- Jamais d'achat de vues : bannissement définitif des plateformes de clipping.

## Réglages

Variables de dépôt facultatives (Settings → Variables) : `GAME_NAMES` (noms de catégorie Twitch,
séparés par des virgules) et `RADAR_LANG` (`fr` par défaut).
