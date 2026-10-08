# Bodet Player

Application web en français pour sélectionner des zones et envoyer l’audio d’un PC, d’un microphone ou d’un fichier au serveur Linux. Docker Compose démarre un seul service web, sans proxy, en HTTP, derrière votre reverse proxy HTTPS.

## État de la connexion Bodet

**Le mode `bodet` est implémenté et activé dans la configuration fournie.** Il encode le son en MP3 et émet les paquets MEL multicast décrits par sigma-caster / be-a-sigma. La réception sur vos enceintes reste à valider. Le mode `simulation` reste disponible sans émission réseau. Les noms des trois zones sont des exemples à remplacer.

Voir [le protocole, sa configuration et ses limites](docs/bodet-protocol.md). Cette intégration indépendante reprend le format décrit par le projet open source ; elle nécessite une validation sur site.

- [Notice officielle Harmonys Stream](https://static.bodet-time.com/images/stories/Pdfs/EN/Manuals/Harmonys/608151-User-manual-Stream-Apps-FR-EN.pdf)
- [Capture audio du navigateur](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)

## Installation et port

Copier `.env.example` vers `.env` si ce fichier n’existe pas encore. Conserver votre mot de passe existant lors d’une mise à jour.

```env
PLAYER_HOST=127.0.0.1
PLAYER_PORT=8080
PLAYER_PASSWORD=votre-mot-de-passe
PLAYER_ORIGIN=
```

`PLAYER_PORT` choisit le port web (1024 à 65535). `PLAYER_HOST` indique l’IP utilisée dans le navigateur, sans protocole ni port ; utiliser l’IP LAN du serveur pour un accès depuis un autre PC. Ouvrir `http://<PLAYER_HOST>:<PLAYER_PORT>`.

Sur Linux, le serveur utilise le réseau hôte pour permettre le multicast Bodet :

```sh
docker compose up -d --build --remove-orphans
```

Sur Windows, utiliser Docker Desktop en mode conteneurs Linux ; le port est publié explicitement :

```powershell
docker compose -f compose.windows.yaml up -d --build --remove-orphans
```

`--remove-orphans` retire l’ancien conteneur proxy lors de la mise à jour. Les paramètres multicast restent dans le volume `player_data`. Aucun certificat Caddy n’est généré par le projet. Les anciens volumes de certificats ne sont pas supprimés automatiquement.

Pour un test local sur Windows, choisir `PLAYER_HOST=127.0.0.1` puis ouvrir `http://127.0.0.1:8080` (adapter le port). En HTTP sur une IP LAN, la connexion à l’interface fonctionne mais le navigateur ne permet pas la capture audio : cette capture nécessite HTTPS ou une adresse de boucle locale sur le PC qui héberge l’application.

## Votre reverse proxy

L’application ne contient aucun proxy ni serveur HTTPS. Configurer votre reverse proxy pour transmettre les requêtes vers `http://<IP_DU_SERVEUR>:<PLAYER_PORT>` et autoriser les connexions WebSocket (`/api/live`). Si votre proxy est un conteneur sur le même réseau Docker, utiliser `http://player:<PLAYER_PORT>` ; son propre `127.0.0.1` désigne le conteneur proxy.

Définir dans `.env` l’adresse publique exacte ouverte dans le navigateur, sans chemin ni barre oblique finale :

```env
PLAYER_HOST=192.168.1.50
PLAYER_PORT=8080
PLAYER_ORIGIN=https://192.168.1.50
```

Si votre proxy publie HTTPS sur 8443, utiliser `PLAYER_ORIGIN=https://192.168.1.50:8443`. `PLAYER_PORT` reste le port HTTP de l’application en amont du proxy. Le certificat est géré par votre proxy et doit être approuvé sur les PC clients pour la capture audio. `PLAYER_ORIGIN` configure les adresses autorisées ; laisser cette variable vide pour un test HTTP direct sur `http://PLAYER_HOST:PLAYER_PORT`.

Pour un accès local en HTTP et distant en HTTPS, séparer les origines par des virgules :

```env
PLAYER_ORIGIN=http://172.17.150.125,https://player.example.fr
```

Remplacer le domaine par celui de votre reverse proxy. Ajouter le port à chaque adresse s’il n’est pas le port standard, par exemple `http://172.17.150.125:8080`. Les espaces autour des virgules sont acceptés ; les chemins, jokers et adresses autres que HTTP/HTTPS sont refusés. Une ancienne configuration avec une seule origine reste compatible. Chaque origine déclarée peut se connecter, contrôler la lecture et ouvrir la connexion audio WebSocket. Les cookies des sessions HTTPS restent `Secure`, avec un nom distinct des cookies HTTP pour permettre les deux accès sur le même nom de serveur. Aucune confiance n’est accordée aux en-têtes de proxy pour décider de cette protection : le protocole de l’origine autorisée utilisée à la connexion fait foi.

Le HTTP local permet la lecture YouTube et de la bibliothèque côté serveur. La capture du microphone ou du son du PC nécessite toujours HTTPS, une adresse locale de boucle ou une exception de confiance du navigateur.

Après modification de `.env`, relancer Compose. Restreindre l’accès au port HTTP au reverse proxy si nécessaire. Le mot de passe est commun aux opérateurs ; les sessions durent huit heures et sont invalidées au redémarrage. La capture sur une IP LAN nécessite l’accès HTTPS par votre proxy. Le test local `http://127.0.0.1:8080` fonctionne sur le PC hébergeant l’application. La configuration fournie active la diffusion Bodet : vérifier les zones et le multicast avant de démarrer. L’émission multicast depuis Docker Desktop vers le LAN reste à valider ; privilégier Linux en réseau hôte pour la diffusion.

## Utilisation

Cliquer sur le bouton bleu **Paramètres** en bas à droite. Dans **Configuration réseau**, saisir manuellement l’adresse multicast IPv4 de l’installation et cliquer sur **Enregistrer l’adresse**. La valeur initiale d’exemple est `239.192.55.1` : vérifier qu’elle correspond à Sigma. Seules les adresses de `224.0.0.0` à `239.255.255.255` sont acceptées. Arrêter toute diffusion avant de changer l’adresse. Le paramètre est conservé après redémarrage dans le volume Docker `player_data` ; `docker compose down -v` efface ce volume. Le mode simulation n’envoie toujours aucun paquet vers les enceintes.

Ouvrir l’interface sur le PC source, se connecter, sélectionner les zones puis une source et démarrer. Le partage du son système dépend du navigateur et du système : choisir une surface proposant l’audio et activer le partage audio. Un onglet audio peut être utilisé si la capture système n’est pas proposée. Le navigateur impose une autorisation à chaque partage ; un autre PC ne peut pas capturer le son à distance sans participation du PC source.

Les trois sources sont **Micro**, **Playlist** et **Onglet externe**. Pour le micro et l’onglet, garder la page ouverte : sa fermeture coupe la session. Pour partager le son d’un onglet, utiliser Chrome ou Edge et cocher le partage audio ; Firefox ne fournit pas ce son. La playlist permet de lire YouTube depuis le serveur dans Firefox. Un opérateur connecté peut arrêter la diffusion depuis son interface. Une seule source peut diffuser à la fois.

Dans le même panneau, **Gérer les zones** permet d’ajouter, modifier ou supprimer une zone. Chaque zone possède un numéro unique entre 1 et 100 et un nom. Les zones sont conservées avec l’adresse multicast dans le volume Docker. Les réglages sont bloqués pendant une diffusion. Les changements mettent à jour la liste des zones sans recharger la page ; ils ne reconfigurent pas la centrale Sigma.

## YouTube, playlists et bibliothèque

Choisir **Playlist**, coller le lien d’une vidéo ou d’une playlist publique, sélectionner les zones puis cliquer sur **Lire le lien YouTube**. Le serveur charge la liste avec **yt-dlp**, télécharge le son de chaque piste au moment de la lire et diffuse via FFmpeg et la passerelle Bodet. Le lecteur propose **Précédent**, **Suivant**, **Arrêter** et trois modes de boucle : désactivée, piste actuelle ou playlist entière. Le passage manuel précédent/suivant revient à l’autre extrémité de la file lorsqu’on atteint une limite. Une vidéo indisponible est signalée et la lecture passe à la suivante ; une file entièrement indisponible s’arrête.

La **file de lecture** se réordonne par glisser-déposer ou avec les flèches, sans interrompre la piste en cours. Après le réordonnancement, le passage automatique suit le nouvel ordre. Les doublons d’une playlist sont conservés.

**Mes playlists** permet d’enregistrer une file sous un nom, puis de la recharger avec le sélecteur. Pour modifier une playlist, charger celle-ci, ajouter ou retirer des sons, réordonner la file puis enregistrer les modifications. **Nouvelle playlist** prépare une file vide. Les playlists sont conservées dans `/data/playlists.json`, avec une limite de 100 playlists de 100 pistes chacune. Un même son peut appartenir à plusieurs playlists sans dupliquer le fichier. Supprimer une playlist conserve tous les sons de la bibliothèque.

Dans la bibliothèque, cocher plusieurs sons ou utiliser **Tout sélectionner**, puis **Ajouter la sélection à la file** ou **Supprimer la sélection**. La suppression multiple demande confirmation, efface les fichiers et retire leurs références de toutes les playlists. La suppression individuelle fait de même. Arrêter la diffusion avant de modifier les playlists ou de supprimer des sons.

Les sons restent dans la **bibliothèque** du volume `player_data` (`/data/youtube/library`). Pour les relire sans Internet, ajouter les pistes à une file, choisir la source **Playlist**, régler leur ordre et démarrer. Les téléchargements terminés sont conservés après arrêt et redémarrage ; la file active et la lecture ne sont pas restaurées après un redémarrage du serveur. La suppression d’une piste de la bibliothèque nécessite l’arrêt de la diffusion. `docker compose down -v` efface aussi ces fichiers.

Les sons importés et YouTube sont diffusés directement par le serveur : Firefox et HTTP fonctionnent sans partage d’écran, sans microphone et sans exception de contexte sécurisé. Fermer la page ou se déconnecter n’arrête pas cette lecture ; utiliser **Arrêter** pour la couper. Une seule source (YouTube, bibliothèque ou capture navigateur) peut diffuser à la fois.

Dans **Playlist**, déposer les sons ou cliquer sur **Importer des sons**. Les imports acceptent plusieurs fichiers, 100 Mio par fichier, avec conversion MP3 et conservation de la première heure de chaque son. Les sons sont conservés sur le serveur et ajoutés automatiquement à la file. Arrêter la diffusion avant un import. La bibliothèque est limitée à 1 Gio. Le dépôt ne déclenche aucune lecture dans le navigateur.

Limites : 100 pistes par file, une heure et 100 Mio par piste, bibliothèque de 1 Gio. Le téléchargement est limité à cinq minutes par piste et peut être interrompu avec Suivant ou Arrêter. Les fichiers temporaires sont nettoyés à l’arrêt normal. Une piste trop volumineuse ou un direct n’est pas téléchargé. Les liens avec un paramètre `list` chargent la playlist entière (dans la limite de 100 pistes). Les vidéos privées, les restrictions YouTube et les demandes de connexion peuvent empêcher le téléchargement ; le message yt-dlp est affiché dans le lecteur. Aucun cookie de compte n’est utilisé.

L’image inclut FFmpeg, Node.js 22 et `yt-dlp[default]` avec ses composants JavaScript. Le serveur doit pouvoir accéder à Internet pour de nouveaux téléchargements. Mettre à jour et reconstruire l’image :

```sh
git pull
docker compose up -d --build --force-recreate
```

Si YouTube change et que yt-dlp nécessite une mise à jour, reconstruire les dépendances avec `docker compose build --no-cache`, puis `docker compose up -d --force-recreate`.

## Délai de diffusion

Le navigateur demande une faible latence audio. La passerelle limite l’analyse initiale de FFmpeg et transmet les blocs MP3 disponibles sans attendre de remplir 1000 octets (125 ms à 64 kbit/s). Le rythme de diffusion et le format MEL restent conservés. Le délai réel dépend aussi du navigateur, du réseau et des buffers des enceintes ; il doit être mesuré sur l’installation. Pour appliquer une mise à jour sur le serveur : `docker compose up -d --build --force-recreate`.

Le panneau **Paramètres → Buffer audio** permet de régler la taille des blocs et l’attente maximale en millisecondes. Trois profils sont proposés : **Faible délai** (10 / 100 ms), **Équilibré** (20 / 200 ms) et **Réseau instable** (40 / 500 ms). Arrêter la diffusion, choisir les valeurs puis cliquer sur **Enregistrer le buffer**. Les réglages sont conservés dans le volume Docker, restent présents après une modification des zones ou du multicast, et s’appliquent à la prochaine diffusion sans redémarrage. Ils sont utilisés par la capture navigateur, le décodage des playlists et la passerelle Bodet.

Les valeurs enregistrées dans l’interface sont prioritaires sur les valeurs initiales de `.env` :

```env
PLAYER_AUDIO_BLOCK_MS=20
PLAYER_AUDIO_MAX_BACKLOG_MS=200
```

`PLAYER_AUDIO_BLOCK_MS` fixe la taille des blocs PCM du navigateur et du décodage serveur, ainsi que le maximum de données MP3 lues avant un envoi multicast (limité à 1000 octets par paquet). Valeurs de 10 à 100 ms ; valeur initiale 20 ms. Pour un réseau stable, essayer 10 ms. Des blocs plus petits augmentent le nombre de messages et paquets. Ils ne réduisent pas la durée des trames du codec MP3 ni le buffer des enceintes.

`PLAYER_AUDIO_MAX_BACKLOG_MS` borne le son en attente d’envoi dans le navigateur et règle le seuil de contre-pression des pipes serveur. La file de réception WebSocket reste limitée à un bloc pour éviter une attente supplémentaire indépendante des réglages. Valeurs de 40 à 1000 ms, au moins deux fois la taille du bloc ; valeur initiale 200 ms. Pour privilégier une faible latence, essayer 100 ms. Lorsque le navigateur dépasse cette limite, la capture s’arrête avec un message plutôt que d’accumuler du retard. Augmenter la limite en cas de coupures sur un réseau instable. Ce sont des limites par étape, et non une promesse de délai total ou un délai ajouté volontairement.

La cadence multicast conserve une horloge audio continue pour éviter que les petits retards d’ordonnancement ne s’additionnent pendant une longue diffusion. Le buffer interne des enceintes Harmonys reste indépendant et n’est pas piloté par cette application. Le temps de téléchargement initial YouTube est également distinct du buffer de lecture. La réception et le délai final doivent être vérifiés sur les enceintes après un changement.

Les fichiers Compose fournis transmettent ces variables au conteneur. Si vous utilisez un Compose personnalisé, ajoutez sous `environment` :

```yaml
      PLAYER_AUDIO_BLOCK_MS: ${PLAYER_AUDIO_BLOCK_MS:-20}
      PLAYER_AUDIO_MAX_BACKLOG_MS: ${PLAYER_AUDIO_MAX_BACKLOG_MS:-200}
```

### Son du PC dans Firefox

Firefox ne fournit pas de piste audio avec le partage d’écran ou d’onglet. Choisir **Son du PC via une entrée audio (Firefox)**, puis **Autoriser et rechercher les entrées audio**. Autoriser l’accès aux entrées et sélectionner explicitement **Mixage stéréo**, si le pilote Windows le propose, ou une entrée de câble audio virtuel déjà configurée pour recevoir le son de YouTube. Démarrer ensuite la diffusion. Aucune entrée de bouclage n’est installée par l’application ; un microphone ordinaire ne remplace pas le bouclage. Tous les sons envoyés vers l’entrée choisie sont transmis. HTTPS (ou l’exception locale de confiance du navigateur) reste nécessaire. Chrome/Edge permettent le partage direct d’un onglet avec son audio.

## Passerelle externe facultative

Le mode `bridge` lance le programme configuré dans `bridge_command` (tableau d’arguments, sans shell). Le programme doit être inclus dans une image personnalisée et accessible à l’utilisateur 10001. Il reçoit :

- sur l’entrée standard : PCM mono signé 16 bits little endian, 48 kHz, flux continu ;
- `BODET_ZONES` : tableau JSON des numéros de zones ;
- `BODET_MULTICAST_ADDRESS` : adresse multicast enregistrée depuis l’interface, figée pour la durée de la diffusion ;
- `AUDIO_FORMAT=s16le`, `AUDIO_RATE=48000`, `AUDIO_CHANNELS=1`.

Cette passerelle doit gérer le protocole Bodet réel, le début et la fin de diffusion, les priorités et les zones. Elle doit libérer la diffusion lorsqu’elle reçoit SIGTERM ou quand l’entrée se ferme. Ce contrat ne fournit pas cette implémentation. Aucun exécutable arbitraire ne peut être configuré depuis l’interface web. Les fichiers de configuration sont montés en lecture seule.

## Vérification et exploitation

```sh
docker compose config
docker compose logs -f player
docker compose down
```

Tests locaux : `python -m pytest tests`. Installer `pytest` et `httpx` en plus de `requirements.txt` dans l’environnement de développement. Les tests couvrent l’authentification, les zones, l’exclusion de deux diffusions et l’arrêt. La validation sur les enceintes et le protocole restent nécessaires avant tout usage réel.

Les tests couvrent aussi les playlists, les boucles, les contrôles YouTube, le réordonnancement, la persistance de la bibliothèque, les limites de téléchargement et le décodage FFmpeg. Les tests réseau utilisent des métadonnées simulées pour rester indépendants de YouTube. Le test d’interface `node tests/ui-smoke.cjs` nécessite Playwright et Microsoft Edge ; il vérifie les commandes, le glisser-déposer, la file de bibliothèque et l’affichage mobile avec une API simulée.
