# AIRI Mini

AIRI Mini est une assistante personnelle en français qui utilise un modèle GGUF local. Tu peux
discuter depuis l’application de bureau, le navigateur ou le terminal, reprendre tes conversations
et indiquer à AIRI ce qu’elle doit retenir.

[Application de bureau](#application-de-bureau) · [Navigateur et terminal](#navigateur-et-terminal) · [Conversations et mémoire](#conversations-et-mémoire) · [Configuration](#configuration) · [Dépannage](#dépannage) · [Développement](#développement)

## Application de bureau

Les paquets se trouvent dans `dist/`. Ils embarquent Electron, Python et le moteur GGUF pour le CPU.

| Plateforme | Fichier | Lancement |
|---|---|---|
| Windows x64 | `AIRI-Mini-*-win-x64.exe` | Lance l’installateur. Il est fourni sans signature. |
| Windows x64 (portable) | `AIRI-Mini-*-win-x64.zip` | Extrais l’archive, puis lance `AIRI Mini.exe` dans le dossier extrait. |
| Linux x64 | `AIRI-Mini-*-linux-x64.AppImage` | Rends le fichier exécutable, puis ouvre-le. |

Au premier lancement, clique sur **Choisir un fichier GGUF** et sélectionne ton modèle local. Une
fois le modèle chargé, tu peux discuter hors ligne. Pour en choisir un autre, ouvre **Paramètres →
Changer de modèle GGUF…**.

Dans **Paramètres**, tu peux choisir une apparence claire ou sombre, ou suivre le système.
L’installateur Windows ajoute aussi un raccourci **AIRI Mini** au menu Démarrer de Windows.

### Reprendre des données existantes

Le plus simple : dans l’ancienne installation, ouvre **Paramètres → Exporter toutes mes données**.
Dans la nouvelle, choisis **Paramètres → Importer des données…** et sélectionne ce fichier. AIRI
affiche d’abord ce qu’elle va importer, puis attend ta confirmation :

- **Importer conversations et souvenirs** ajoute les conversations absentes et les nouveaux
  souvenirs, sans rien écraser ;
- **Tout importer et remplacer le profil** remplace aussi l’identité, la personnalité, l’humeur, la
  relation, le profil et l’héritage. Une copie `.bak` de chaque fichier remplacé est conservée.

Le fichier est entièrement vérifié avant toute écriture. Il ne contient pas `config/airi.json`, dont
le chemin du modèle dépend de chaque machine.

Pour une copie manuelle :

1. Dans l’application de bureau, ouvre **Paramètres → Ouvrir le dossier de données**, puis ferme l’application.
2. Si tu as déjà utilisé cette version, fais une copie du dossier de données affiché.
3. Copie le contenu de ton ancien dossier `data/` dans le nouveau dossier `data/`. Si tu as personnalisé les réglages, copie aussi les fichiers de `config/` dans le dossier correspondant.
4. Relance AIRI Mini et sélectionne de nouveau ton modèle si son chemin était relatif à l’ancien projet.

## Navigateur et terminal

Cette installation requiert Python 3.11 ou ultérieur. Elle a été vérifiée avec Python 3.14. Depuis
la racine du projet, prépare l’environnement et crée les fichiers de données manquants.

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python init_data.py
```

`init_data.py` crée les fichiers manquants depuis `data.example/` et conserve les fichiers existants. En l’absence de paquet binaire compatible, `llama-cpp-python` peut nécessiter un compilateur C/C++ et CMake. L’accélération GPU demande un moteur compilé avec le support correspondant.

### Modèle GGUF

Place un modèle compatible dans `models/`, puis indique son chemin dans `config/airi.json`. Le
chemin par défaut est `models/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` (environ 4,2 Go). Le modèle est à
fournir localement. Prévois aussi de la RAM pour le contexte, Python et le système, et vérifie les
conditions de la licence du modèle choisi.

### Lancer AIRI

**Dans le navigateur**, démarre le serveur avec `./venv/bin/python app.py`. Par défaut, il écoute sur `http://127.0.0.1:8000` et ouvre le navigateur une fois le modèle chargé.

**Dans le terminal**, utilise les commandes suivantes pour ouvrir, lister ou reprendre une conversation.

```bash
./run_cli.sh
./run_cli.sh --list
./run_cli.sh --session UUID_DE_CONVERSATION
```

AIRI verrouille le dossier de données pendant une session Web, terminal ou Electron. Si le dossier
est déjà utilisé, ferme l’autre instance avant de réessayer. La commande `./run_cli.sh --list`
reste disponible en lecture seule. Laisse le fichier `data/.airi.lock` en place. Le système libère le
verrou à la fermeture du processus.

Utilise un seul worker Uvicorn, sans rechargement automatique. Le serveur confie les opérations à un
worker dédié pour que l’interface HTTP reste disponible pendant la génération.

## Conversations et mémoire

### Conversations

L’application de bureau et l’interface Web permettent de créer, reprendre, renommer, supprimer et
exporter des conversations en JSON. Le champ **Chercher…** de la barre latérale trouve un mot dans les
titres et les messages de toutes les conversations, sans tenir compte des majuscules ni des accents.
La suppression demande un second clic et efface aussi la sauvegarde `.bak` de la conversation. Elles mémorisent la dernière conversation sélectionnée. Les messages sont
stockés dans `data/sessions/`. Si deux onglets affichent la même conversation, recharge-la pour voir
les échanges ajoutés dans l’autre onglet.

Les réponses apparaissent progressivement. Le bouton carré **Arrêter** interrompt la réponse : ce
qui a déjà été écrit est enregistré et marqué « réponse interrompue ». Après une coupure de
connexion, AIRI enregistre de la même façon la partie déjà générée. **Regénérer** remplace la dernière
réponse, et **Modifier mon dernier message** permet de corriger ta question et d’obtenir une nouvelle
réponse. Si la nouvelle génération échoue avant d’écrire quoi que ce soit, l’ancien échange
est conservé ; si tu l’arrêtes en cours, c’est la nouvelle réponse partielle qui est gardée. Les commandes qui modifient
la mémoire ou la relation prennent effet avant la sauvegarde de la réponse de confirmation.

### Commandes

Le panneau **Souvenirs** permet d’ajouter ou de retirer directement des faits partagés. Les
commandes suivantes fonctionnent aussi dans le chat.

| Commande | Effet |
|---|---|
| `/aide` | Afficher les commandes |
| `/retiens Je travaille sur AIRI.` | Enregistrer un souvenir partagé |
| `/souvenirs` | Consulter la première page de souvenirs |
| `/souvenirs 2` | Consulter la page 2 |
| `/oublie 3` | Retirer le souvenir numéro 3 de `/souvenirs` |
| `/oublie Je travaille sur AIRI.` | Retirer ce souvenir exact |
| `/activite programmation` | Définir l’activité de cette conversation |
| `/activite` | Effacer l’activité courante |
| `/humeur joyeuse` | Demander un ton pour cette conversation |
| `/humeur` | Revenir au ton initial |
| `/familiarite 0.7` | Définir la familiarité globale entre 0 et 1 |
| `/sujet Python` | Ajouter un sujet récurrent global |
| `/moment Première version terminée` | Ajouter un moment important global |

`/souvenirs` affiche jusqu’à dix souvenirs numérotés par page et indique la commande pour passer à la
suivante. Après une suppression, les numéros suivants se décalent : relance `/souvenirs` avant
d’en supprimer un autre.

Quand tu partages une information durable, AIRI peut proposer de la retenir. Une carte
« Retenir ce souvenir ? » s’affiche alors sous sa réponse. Rien n’est enregistré tant que tu ne
cliques pas sur **Retenir**. En ligne de commande, la suggestion indique la commande `/retiens` à
taper. Désactive ces propositions avec `suggest_memories`.

AIRI accepte aussi `Retiens ceci : …` et `Oublie ceci : …`. Les souvenirs, l’humeur et la
familiarité changent sur demande explicite. Le ton et la familiarité sont des préférences déclarées.

Pour effacer un fait partout, retire-le de la mémoire active et des copies concernées. Il peut
encore figurer dans les anciens messages, les exports et les sauvegardes `.bak`.

## Configuration

Dans l’application empaquetée, ouvre **Paramètres → Ouvrir le dossier de données** pour accéder aux
réglages. Le navigateur, le terminal et Electron en mode développement utilisent les fichiers de la
racine du projet. Les chemins relatifs partent du dossier qui contient `config/` et `data/`.
Redémarre AIRI après une modification manuelle.

### Modèle et serveur

`config/airi.json` contrôle le modèle, le contexte et les performances. AIRI signale les clés inconnues et les valeurs incompatibles au démarrage.

| Option | Valeur initiale | Usage |
|---|---|---|
| `model_path` | [Chemin du modèle par défaut](#modèle-gguf) | Chemin absolu ou relatif au dossier AIRI |
| `n_ctx` | `4096` | Fenêtre de contexte en tokens |
| `max_tokens` | `512` | Nombre maximal de tokens par réponse |
| `temperature` | `0.7` | Variation des réponses |
| `n_threads` | `6` | Threads de calcul CPU |
| `n_batch` | `256` | Taille des lots de traitement |
| `n_gpu_layers` | `0` | Couches GPU. `-1` utilise toutes les couches avec un moteur compatible. |
| `max_message_chars` | `8000` | Plafond de caractères par message |
| `max_history_messages` | `40` | Nombre de messages récents candidats au contexte, par paires |
| `max_saved_messages` | `1000` | Nombre de messages conservés par conversation, par paires |
| `suggest_memories` | `true` | Laisser AIRI proposer des souvenirs, enregistrés seulement après ta confirmation |
| `summarize_history` | `true` | Résumer les anciens échanges d’une conversation quand le contexte est plein, au lieu de les oublier |
| `summary_max_tokens` | `300` | Longueur maximale de ce résumé, plafonnée au quart du contexte disponible |
| `port` | `8000` | Port de l’interface Web ouverte avec `app.py` |
| `open_browser` | `true` | Ouvrir le navigateur après le chargement avec `app.py` |
| `language` | `fr` | Langue des messages, des commandes et des prompts (fichier `core/locales/<langue>.py`) |

Electron choisit automatiquement un port local pour son moteur. Pour le contexte, AIRI réserve
`max_tokens` à la réponse et garde une marge pour le gabarit de chat. Elle retire l’historique
ancien par échanges entiers si nécessaire. Les souvenirs entrent dans le budget par ordre de
pertinence (mots en commun avec ton message et le précédent, classement BM25), puis du plus récent
au plus ancien. Un message qui dépasse ce budget déclenche une erreur.

Avec `summarize_history`, les échanges retirés ne sont pas perdus : le modèle les résume dans un
résumé glissant propre à la conversation, stocké dans son fichier (`summary`) et jamais partagé avec
les autres conversations ni ajouté aux souvenirs. Le résumé est mis à jour par blocs, pas à chaque
message. Si le résumé échoue, AIRI retire simplement les anciens échanges comme auparavant.

Le fichier actif conserve les `max_saved_messages` derniers messages. Exporte une conversation avant
ce seuil si tu veux garder tous ses échanges. Tu peux créer autant de conversations que nécessaire.

### Personnalité et profil

| Fichier | Rôle |
|---|---|
| `config/personality.json` | Ton, style, humour et traits de personnalité |
| `data/identity.json`, `data/mood.json`, `data/relationship.json` | Identité, humeur et paramètres de relation, validés au démarrage |
| `data/user_profile.json`, `data/heritage_haru.json` | Objets JSON utilisés comme contexte |

Le prompt encourage l’honnêteté, le respect de l’autonomie et l’usage de souvenirs déclarés. AIRI
n’a aucun outil d’exécution système.

Ton prénom n’est pas écrit dans le code. AIRI le lit dans `data/user_profile.json` (clé `nom`, ou
`name`), sinon dans le premier élément de `communication.address_user_as` de `data/identity.json`.
Sans aucun des deux, elle parle de « l’utilisateur ». Le nom d’AIRI elle-même vient du champ `name`
de `data/identity.json`. Pour adapter le projet à une autre personne, modifie ces fichiers ainsi que
les mentions du prénom dans `config/personality.json` et `data/identity.json`.

### Langues

Les textes destinés à l’utilisateur ou au modèle sont regroupés dans `core/locales/fr.py` :
messages d’erreur, réponses aux commandes, noms des commandes (`/retiens`…), morceaux de prompt et
mots ignorés par la recherche de souvenirs. Pour ajouter une langue :

1. Copie `core/locales/fr.py` vers `core/locales/<code>.py`, par exemple `en.py`.
2. Traduis les valeurs, sans changer les clés ni les `{repères}` entre accolades.
3. Règle `"language": "<code>"` dans `config/airi.json`.

Une clé absente de la traduction reprend le texte français, ce qui permet de traduire petit à petit.
L’interface Web (`web/`) et les journaux du serveur restent en français pour l’instant.

## Dépannage

| Situation | Action |
|---|---|
| L’AppImage est bloquée par un problème de FUSE. | Lance le fichier AppImage depuis le terminal en ajoutant l’option `--appimage-extract-and-run`. |
| Le moteur échoue sur Windows et le runtime Visual C++ est absent. | Installe le redistribuable Microsoft Visual C++ v14 x64. [Téléchargement Microsoft](https://learn.microsoft.com/fr-fr/cpp/windows/latest-supported-vc-redist) |
| Le modèle est introuvable ou un réglage est invalide. | Dans Electron, ouvre **Paramètres → Ouvrir le journal**. Pour le Web et le terminal, lis la sortie du terminal. |
| Le serveur Web a redémarré. | Recharge la page pour renouveler le jeton local. |
| La connexion s’est coupée pendant une commande de mémoire. | Vérifie le résultat dans **Souvenirs** avant de répéter la commande. |

### Restaurer un fichier JSON

AIRI remplace les fichiers JSON de façon atomique et garde la version précédente dans
`nom.json.bak`. Une conversation corrompue reste signalée dans la liste pendant que les autres
restent accessibles.

Si AIRI signale un échec de synchronisation après la mise à jour d’un fichier, la modification est
déjà visible sur disque et AIRI en tient compte. Sa conservation après un arrêt brutal reste
incertaine. Rouvre les souvenirs ou l’historique pour vérifier avant de répéter l’action.

1. Arrête AIRI et conserve une copie du fichier concerné.
2. Vérifie sa sauvegarde `.bak`.
3. Restaure une version JSON valide, puis relance AIRI.

Les nouveaux fichiers et sauvegardes utilisent les permissions du dossier utilisateur sur Windows et
des permissions de fichier restreintes sur Linux. Les journaux indiquent notamment le chargement,
les sauvegardes et les erreurs. Ils ne consignent pas systématiquement les messages et réponses.

## Développement

### Structure du projet

| Emplacement | Rôle |
|---|---|
| [`app.py`](app.py) et [`web/`](web/) | Serveur FastAPI et interface de chat |
| [`cli.py`](cli.py) | Interface en ligne de commande |
| [`core/`](core/) | Moteur GGUF, conversations et mémoire |
| [`desktop/`](desktop/) et [`desktop_backend.py`](desktop_backend.py) | Fenêtre Electron, sélection du modèle et lancement du moteur local |
| [`config/`](config/) et [`data.example/`](data.example/) | Réglages et données initiales d’exemple |
| [`tests/`](tests/) | Tests Python et JavaScript |

L’API est réservée à l’usage local. Elle vérifie l’hôte et exige un jeton pour les modifications.
Dans Electron, toutes les requêtes demandent aussi un jeton propre au lancement. L’interface charge
ses ressources et ses polices sur l’appareil.

`.gitignore` exclut les données personnelles, les modèles, les sauvegardes, les environnements virtuels et les sorties de compilation. Vérifie aussi l’index avant une publication si des fichiers de ces dossiers ont déjà été suivis.

### Lancer et empaqueter Electron

Utilise Node.js 22.12 ou ultérieur et Python 3.14 x64. À la racine du projet, lance `npm ci`, puis
`python -m venv venv` si l’environnement Python n’existe pas encore.

**Linux**

```bash
./venv/bin/python -m pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --only-binary=llama-cpp-python -r requirements-desktop.txt
npm start
```

**Windows (PowerShell)**

```powershell
.\venv\Scripts\python.exe -m pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --only-binary=llama-cpp-python -r requirements-desktop.txt
npm start
```

`npm start` lance Electron en développement avec les données et les réglages du projet. Pour produire les paquets, ferme la fenêtre, puis exécute la commande sur le système cible.

| Système hôte | Commande | Sortie dans `dist/` |
|---|---|---|
| Linux x64 | `npm run dist:linux` | AppImage |
| Windows x64 | `npm run dist:win` | Installateur `.exe` et archive `.zip` |

Les scripts compilent le moteur natif et incluent les réglages et données d’exemple dans les
paquets.

### Exécuter les tests

```bash
./venv/bin/python -m unittest discover -s tests -v
node --check web/app.js
node --check web/menus.js
node --test tests/test_web.cjs
```

`pytest` n’est pas nécessaire. Les tests n’ont pas besoin de `llama-cpp-python` ni du modèle GGUF.
Le workflow [`.github/workflows/tests.yml`](.github/workflows/tests.yml) lance ces mêmes commandes sur GitHub à chaque `push` et `pull_request`.

Les tests Python utilisent un faux modèle et des fichiers temporaires. Ils couvrent les
conversations, la mémoire, les sauvegardes, le budget de contexte et l’API HTTP. Les tests HTTP
ouvrent un port temporaire sur `127.0.0.1`. Les tests JavaScript simulent le DOM pour vérifier le
flux de réponse, les envois en double et la reprise après erreur.
