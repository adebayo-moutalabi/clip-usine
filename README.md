# Clip Usine

Fabrique des shorts verticaux montés à partir de clips et de VOD, sans rien installer.
Le montage tourne sur les serveurs gratuits de GitHub ; tout se pilote depuis une page web.

**Le studio : https://adebayo-moutalabi.github.io/clip-usine/**

## Ce que fait le montage

- Recadrage 9:16 : fond flouté, plein écran, ou facecam en haut et jeu en bas
- Repérage automatique des temps forts d'une VOD (pics sonores : rires, cris, action)
- Montage best-of : plusieurs temps forts réunis en un clip, avec fondus variés
- Style dynamique : zoom sur chaque pic sonore, lente avancée de caméra, sous-titres mot à mot
  avec le mot prononcé surligné, titre d'accroche plein écran, barre de progression, fondus d'entrée et de sortie
- Son normalisé au niveau attendu par les plateformes
- Une fiche par clip : contrôles avant publication et prompt Claude pour titres, légendes, hashtags

## Organisation du dépôt

| Dossier | Rôle |
|---|---|
| `site/` | La page web du studio |
| `scripts/make_clip.py` | Le montage |
| `scripts/publish.py` | Met le site et les clips en ligne (branche `gh-pages`) |
| `scripts/radar.py` | Radar Twitch, facultatif |
| `.github/workflows/` | Les deux automatisations |

## Mise en route

1. **Activer la page** (une fois) : Settings → Pages → « Deploy from a branch » → branche `gh-pages`, dossier `/ (root)`.
2. **Créer la clé d'accès** : les 5 étapes sont dans l'onglet Aide du studio.
3. Dans le studio : « Utiliser la vidéo de test », puis « Fabriquer ».

## Règles

- Ne clippe que ce qu'on t'autorise : brief de campagne qui cite la source, ou accord écrit du streamer.
- Pas de spoiler, pas d'images d'avant la sortie d'un jeu.
- Clip rémunéré : mention de partenariat visible.
- Jamais d'achat de vues.
