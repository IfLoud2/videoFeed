# Ma liste du jour

Chaque nuit, GitHub récupère les nouvelles vidéos de tes chaînes (flux RSS), retire les Shorts et les vidéos trop courtes, en choisit 5 par règles simples et publie une page web. Quand les 5 ont un avis, la page affiche « Terminé. À demain. ».

## Mise en place

1. Crée une clé API YouTube : console.cloud.google.com, nouveau projet, activer « YouTube Data API v3 », Identifiants, Créer des identifiants, Clé API. Restreins-la à YouTube Data API v3.
2. Crée un dépôt public sur GitHub et envoie-y tout ce dossier (y compris le dossier `.github`).
3. Dans le dépôt : Settings, Secrets and variables, Actions, New repository secret. Nom : `YT_API_KEY`, valeur : ta clé.
4. Settings, Pages : Source « Deploy from a branch », branche `main`, dossier `/docs`.
5. Onglet Actions, workflow « daily-feed », Run workflow. Il tourne ensuite chaque nuit vers 4 h 30.
6. Ta page : `https://TON_PSEUDO.github.io/NOM_DU_DEPOT/`

## Vérifier les chaînes

Au premier lancement, ouvre le journal du workflow : chaque ligne `résolu : nom -> titre (handle|search)` montre la chaîne réellement trouvée. Une ligne `search` signifie que l'identifiant n'a pas été reconnu et que la chaîne a été cherchée par son nom : contrôle le titre. Pour corriger, modifie `handle` dans `config.json` puis supprime l'entrée dans `data/channels_resolved.json`.

## Réglages

`config.json`, section `settings` : nombre de vidéos par jour, durée minimale (480 s = 8 min), ancienneté maximale, maximum par thème.
Pour ajouter une chaîne : une ligne dans `channels` avec `name`, `handle` (la partie après `@` dans l'adresse de la chaîne), `theme`, `lang`.

## Ce que fait chaque fichier

- `build_feed.py` : récupère, filtre, choisit et écrit `docs/today.json`.
- `docs/index.html` : la page. Les avis restent dans le navigateur de l'appareil utilisé.
- `data/` : mémoire du système (chaînes résolues, vidéos déjà proposées, quota API utilisé).
- `.github/workflows/daily.yml` : la tâche de nuit.
