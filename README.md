# Bodet Player

Application web en français pour sélectionner des zones et diffuser l’audio du PC via son agent Windows vers le serveur Linux. Docker Compose démarre un seul service web, sans proxy, en HTTP, derrière votre reverse proxy HTTPS.

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

Le HTTP local permet la lecture des fichiers de la bibliothèque côté serveur. La capture du microphone ou du son du PC nécessite toujours HTTPS, une adresse locale de boucle ou une exception de confiance du navigateur.

Après modification de `.env`, relancer Compose. Restreindre l’accès au port HTTP au reverse proxy si nécessaire. Le mot de passe est commun aux opérateurs ; les sessions durent huit heures et sont invalidées au redémarrage. La capture sur une IP LAN nécessite l’accès HTTPS par votre proxy. Le test local `http://127.0.0.1:8080` fonctionne sur le PC hébergeant l’application. La configuration fournie active la diffusion Bodet : vérifier les zones et le multicast avant de démarrer. L’émission multicast depuis Docker Desktop vers le LAN reste à valider ; privilégier Linux en réseau hôte pour la diffusion.

## Utilisation

Cliquer sur le bouton bleu **Paramètres** en bas à droite. Dans **Configuration réseau**, saisir manuellement l’adresse multicast IPv4 de l’installation et cliquer sur **Enregistrer l’adresse**. La valeur initiale d’exemple est `239.192.55.1` : vérifier qu’elle correspond à Sigma. Seules les adresses de `224.0.0.0` à `239.255.255.255` sont acceptées. Arrêter toute diffusion avant de changer l’adresse. Le paramètre est conservé après redémarrage dans le volume Docker `player_data` ; `docker compose down -v` efface ce volume. Le mode simulation n’envoie toujours aucun paquet vers les enceintes.

Ouvrir l’interface sur le PC source et se connecter. Elle propose uniquement la diffusion avec l’agent Windows local. Si aucun agent n’est détecté, le panneau **Installer l’agent** propose son téléchargement et une vérification locale. Dès que l’agent est détecté, ce panneau se masque et l’écran affiche son état et le bouton **Gérer la diffusion**.

Cliquer sur **Gérer la diffusion**, sélectionner les zones puis **Démarrer la diffusion**. Sans zone sélectionnée, un avertissement rouge visible et annoncé par les lecteurs d’écran demande de choisir au moins une zone ; aucune commande de démarrage n’est envoyée. **Arrêter la diffusion** coupe la capture de ce PC. Fermer, recharger ou quitter l’onglet qui a démarré la diffusion arrête sa capture. Fermer un autre onglet ou passer en arrière-plan ne l’arrête pas. En cas de plantage du navigateur ou de perte réseau, le serveur coupe la capture après au plus huit secondes sans réponse de l’onglet. Le navigateur ne contrôle pas les agents des autres PC. Une seule diffusion peut être active à la fois.

Dans le même panneau, **Gérer les zones** permet d’ajouter, modifier ou supprimer une zone. Chaque zone possède un numéro unique entre 1 et 100 et un nom. Les zones sont conservées avec l’adresse multicast dans le volume Docker. Les réglages sont bloqués pendant une diffusion. Les changements mettent à jour la liste des zones sans recharger la page ; ils ne reconfigurent pas la centrale Sigma.

## Mise à jour

```sh
git pull
docker compose up -d --build --force-recreate
```

Recharger la page avec Ctrl+F5. L’interface ne propose plus les sources micro, onglet, fichiers ni playlists. Les données audio et playlists déjà stockées dans le volume Docker sont conservées ; les routes historiques côté serveur restent disponibles.

## Délai de diffusion

La passerelle limite l’analyse initiale de FFmpeg et transmet les blocs MP3 disponibles sans attendre de remplir 1000 octets (125 ms à 64 kbit/s). Le rythme de diffusion et le format MEL restent conservés. Le délai réel dépend aussi du navigateur, du réseau et des buffers des enceintes ; il doit être mesuré sur l’installation. Pour appliquer une mise à jour sur le serveur : `docker compose up -d --build --force-recreate`.

Le panneau **Paramètres → Buffer audio** permet de régler la taille des blocs et l’attente maximale en millisecondes. Trois profils sont proposés : **Faible délai** (10 / 100 ms), **Équilibré** (20 / 200 ms) et **Réseau instable** (40 / 500 ms). Arrêter la diffusion, choisir les valeurs puis cliquer sur **Enregistrer le buffer**. Les réglages sont conservés dans le volume Docker, restent présents après une modification des zones ou du multicast, et s’appliquent à la prochaine diffusion sans redémarrage. Ils règlent la capture navigateur et le décodage des playlists. Le découpage des paquets multicast Bodet reste indépendant de ces valeurs pour préserver la compatibilité des récepteurs.

Les valeurs enregistrées dans l’interface sont prioritaires sur les valeurs initiales de `.env` :

```env
PLAYER_AUDIO_BLOCK_MS=20
PLAYER_AUDIO_MAX_BACKLOG_MS=200
```

`PLAYER_AUDIO_BLOCK_MS` fixe la taille des blocs PCM du navigateur et du décodage serveur. Il ne change pas le découpage multicast Bodet : la passerelle lit les données MP3 disponibles avec un plafond fixe de 1000 octets, sans attendre de remplir un paquet. Valeurs de 10 à 100 ms ; valeur initiale 20 ms. Pour un réseau stable, essayer 10 ms. Des blocs plus petits augmentent le nombre de messages PCM. Ils ne réduisent pas la durée des trames du codec MP3 ni le buffer des enceintes.

`PLAYER_AUDIO_MAX_BACKLOG_MS` borne le son en attente d’envoi dans le navigateur et règle le seuil de contre-pression des pipes serveur. La file de réception WebSocket reste limitée à un bloc pour éviter une attente supplémentaire indépendante des réglages. Valeurs de 40 à 1000 ms, au moins deux fois la taille du bloc ; valeur initiale 200 ms. Pour privilégier une faible latence, essayer 100 ms. Lorsque le navigateur dépasse cette limite, la capture s’arrête avec un message plutôt que d’accumuler du retard. Augmenter la limite en cas de coupures sur un réseau instable. Ce sont des limites par étape, et non une promesse de délai total ou un délai ajouté volontairement.

La cadence multicast conserve une horloge audio continue pour éviter que les petits retards d’ordonnancement ne s’additionnent pendant une longue diffusion. Le buffer interne des enceintes Harmonys reste indépendant et n’est pas piloté par cette application. La réception et le délai final doivent être vérifiés sur les enceintes après un changement.

Les fichiers Compose fournis transmettent ces variables au conteneur. Si vous utilisez un Compose personnalisé, ajoutez sous `environment` :

```yaml
      PLAYER_AUDIO_BLOCK_MS: ${PLAYER_AUDIO_BLOCK_MS:-20}
      PLAYER_AUDIO_MAX_BACKLOG_MS: ${PLAYER_AUDIO_MAX_BACKLOG_MS:-200}
```

### Son du PC dans Firefox

Firefox utilise l’agent Windows pour diffuser le son du PC. Autoriser l’accès aux applications et services de cet appareil si Firefox le demande. La détection laisse 30 secondes pour répondre à cette demande. Si le PC ne remonte pas, vérifier l’icône de l’agent près de l’horloge et utiliser **Vérifier l’agent** dans le panneau d’installation. Ce panneau se masque automatiquement après détection.

### Agent Windows et contrôle depuis le navigateur

1. Après `git pull`, reconstruire l’image avec `docker compose up -d --build --force-recreate`, puis recharger la page avec Ctrl+F5.
2. Dans le panneau **Installer l’agent**, cliquer sur **Télécharger l’agent pour ce PC**. La construction Docker compile automatiquement `BodetAgent.exe` pour Windows 10/11 x64, avec .NET inclus ; aucune installation de Python ou de .NET n’est nécessaire sur le PC source. La première construction télécharge le SDK et les dépendances de l’agent.
3. Le téléchargement contient l’adresse du serveur et un ticket d’association à usage unique valable 24 heures. Lancer l’exécutable sur le PC source : l’association est automatique, avec le nom Windows du PC. Le ticket est consommé par le premier PC qui l’utilise ; le même téléchargement ne peut pas associer un second PC. Les tickets sont conservés sous forme d’empreintes dans `/data/agent-installations.json` et survivent au redémarrage du serveur. L’exécutable n’est pas signé par un certificat de publication ; une politique Windows exigeant des exécutables signés peut empêcher son lancement.
4. L’agent de ce PC apparaît connecté dans la page. Choisir les zones, puis **Démarrer la diffusion**. **Arrêter toute diffusion** arrête aussi la capture native de ce PC. Le navigateur ne peut pas démarrer, arrêter ni retirer l’agent d’un autre PC. Autoriser l’accès au réseau local si le navigateur le demande pour détecter l’agent. L’agent ne capture aucun son avant cette commande web.
5. Après association, la fenêtre se masque et l’agent reste dans la zone de notification. Fermer sa fenêtre avec la croix le laisse actif. Il démarre automatiquement à l’ouverture de votre session Windows et retente la connexion après une interruption réseau ou un redémarrage du serveur, sans nouveau code d’association. L’onglet qui démarre la diffusion doit rester ouvert : sa fermeture arrête la capture, tandis que l’agent reste disponible en arrière-plan. La capture se démarre depuis la page web ; après une interruption de connexion, l’agent revient disponible et attend une nouvelle commande de diffusion.

L’exécutable est copié dans `%LOCALAPPDATA%/BodetPlayer/BodetAgent.exe` et son démarrage est enregistré pour l’utilisateur courant dans `HKCU/Software/Microsoft/Windows/CurrentVersion/Run` (`BodetPlayerAgent`). Aucun droit administrateur n’est nécessaire. Le fichier téléchargé peut ensuite être supprimé. Une seule instance fonctionne par utilisateur. Pour ouvrir la fenêtre, double-cliquer sur l’icône de notification. **Quitter et arrêter la capture** dans son menu arrête l’agent jusqu’à la prochaine ouverture de session ; **Déconnecter et arrêter la capture** suspend la connexion jusqu’à un clic sur **Reconnecter** ou à la prochaine ouverture de session. **Oublier l’association** supprime les identifiants locaux et désactive le démarrage automatique.

Pour mettre à jour un agent déjà installé, reconstruire le serveur, télécharger la nouvelle version, quitter l’ancien agent depuis son icône puis lancer le nouveau fichier. Il retrouve l’association existante et met à jour sa copie installée. Les agents installés avant cette version doivent être mis à jour une fois pour bénéficier du démarrage automatique.

La capture utilise [WASAPI loopback via NAudio](https://github.com/naudio/NAudio/blob/v2.2.1/Docs/WasapiLoopbackCapture.md) sur la sortie Windows par défaut au moment du démarrage. Tous les sons des applications dirigés vers cette sortie sont transmis : aucun filtrage par application. Changer de sortie nécessite d’arrêter puis de redémarrer la diffusion. Un pilote, une sortie déconnectée ou un contenu protégé peut limiter la capture. Le son est converti en PCM mono 16 bits à 48 kHz et envoyé directement au serveur via WebSocket ; l’audio ne passe pas par le navigateur et n’est pas enregistré sur disque. Le buffer configuré dans les paramètres s’applique aux blocs envoyés et à l’attente maximale ; le délai du pilote audio et des enceintes reste indépendant. Pendant le silence, l’agent envoie des blocs silencieux pour maintenir la session.

L’audio et le contrôle utilisent des connexions sortantes HTTP/WebSocket vers Bodet Player. L’adresse doit être accessible depuis le PC source. Avec HTTPS, le certificat doit être approuvé par Windows ; aucune validation de certificat n’est désactivée. Le reverse proxy doit laisser passer les WebSockets pour `/api/agents/control`, `/api/agents/browser` et `/api/live`. L’agent reçoit uniquement des commandes de capture et d’arrêt, pas de commandes système.

Un point de détection écoute uniquement sur `127.0.0.1:17861`, sans exposition au réseau local et sans règle de pare-feu ajoutée. Il accepte seulement l’origine du serveur configuré et signe un défi à usage unique lié à la session du navigateur ; il ne fournit aucun jeton d’agent au navigateur. Le serveur vérifie cette preuve avant d’autoriser pendant 30 secondes le contrôle de l’agent correspondant. La page renouvelle cette vérification tant qu’elle utilise l’agent. Si l’accès local est refusé ou l’agent arrêté, les commandes sont indisponibles. Les navigateurs ou politiques qui bloquent cet accès local doivent autoriser le site avant utilisation.

Les associations sont conservées dans `/data/agents.json` avec les empreintes des jetons. Le jeton du PC est chiffré par Windows pour l’utilisateur courant dans `%LOCALAPPDATA%/BodetPlayer/agent.dat`. **Retirer** l’agent de ce PC depuis la page révoque son accès et interrompt sa capture. Après une révocation, oublier l’association locale et télécharger un nouvel agent préconfiguré pour le réassocier. La fermeture de l’onglet propriétaire ou la déconnexion de son opérateur arrête la capture. L’onglet envoie un arrêt à sa fermeture et possède une connexion dédiée dont la perte coupe également la capture. L’arrêt est lié à cet onglet et à sa session de diffusion ; une ancienne notification ne peut pas couper une nouvelle diffusion. Une perte de connexion de l’agent ou un dépassement de buffer interrompt aussi la capture.

Pour compiler hors Docker : `dotnet publish windows-agent/BodetAgent.csproj -c Release -r win-x64 --self-contained true -o app/downloads --source https://api.nuget.org/v3/index.json`. Les sources sont dans `windows-agent/` ; le binaire et les fichiers de compilation restent exclus de Git. `BodetAgent.exe --self-test` vérifie la conversion audio et la copie persistante de l’exécutable sans utiliser les périphériques ni inscrire de démarrage automatique. Le test `tests/test_native_agent.py` utilise l’exécutable compilé sur Windows et un signal synthétique avec un serveur local en simulation : démarrage, transmission PCM, arrêt, redémarrage du serveur, reconnexion automatique, maintien de connexion au repos et révocation. La capture réelle et la réception sur les enceintes restent à valider sur l’installation.

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

Les tests couvrent aussi les playlists, les boucles, les contrôles du lecteur, le réordonnancement, la persistance de la bibliothèque, les limites d’import et le décodage FFmpeg. Les tests de lecture utilisent des fichiers audio locaux et des pistes simulées. Le test d’interface `node tests/ui-smoke.cjs` nécessite Playwright et Microsoft Edge ; il vérifie les commandes, le glisser-déposer, la file de bibliothèque et l’affichage mobile avec une API simulée.
