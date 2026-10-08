# Diffusion Harmonys native

Le mode `bodet` utilise une implémentation indépendante du format réseau décrit
par [sigma-caster / be-a-sigma](https://git.teleco.ch/crt/be-a-sigma.git/tree/src/sigma-caster.rs),
consulté le 7 octobre 2026. Aucun code Rust du dépôt n'est intégré au produit.
Il ne s'agit pas d'un SDK officiel. Compatibilité matérielle à vérifier sur site.

Le PCM du navigateur (s16le, mono, 48 kHz) est encodé en continu par FFmpeg / libmp3lame.
Le profil `low` utilise 64 kbit/s à 32 kHz, `high` utilise 256 kbit/s à 48 kHz.
Les blocs MP3 de 1000 octets sont encapsulés dans des datagrammes MEL et envoyés
deux fois chacun à l'adresse configurée, port UDP 1681, TTL multicast 1.
Le masque des zones contient 13 octets, bits de poids faible en premier,
pour les zones 1 à 100. La séquence est un compteur sur un octet ; l'identifiant
de flux est 0x110c. Le checksum sur deux octets est le XOR des sommes
octet + position, limité à 16 bits. La longueur réseau inclut tout le datagramme.

Dans `config/player.json`, `mode` vaut `bodet`, `bodet_quality` vaut `low`
ou `high`. `bodet_interface` peut être l'adresse IPv4 locale de l'interface
du serveur Linux utilisée pour le multicast ; vide, le système choisit la route.
Reconstruire l'image pour installer FFmpeg : `docker compose up -d --build`.
Le mode `simulation` reste disponible pour tester sans émission réseau.

L'arrêt du lecteur termine la passerelle et son encodeur. L'arrêt consiste à
cesser les paquets : sigma-caster ne décrit pas de commande de libération
propre au streaming. Le délai de silence effectif des enceintes reste à mesurer.
Aucune commande globale d'arrêt d'alerte n'est envoyée. La priorité reprend les
octets constants du projet de référence ; elle n'est pas réglable dans ce lecteur.
L'identifiant fixe peut entrer en conflit avec un autre émetteur sigma-caster.

Les tests vérifient les champs réseau, les limites des zones, l'encodage FFmpeg
réel et les duplications via une sortie UDP interceptée, sans diffuser sur le réseau.
Ils ne démontrent pas la réception sur les enceintes.

Validation sur site : sélectionner une enceinte de test, confirmer la réception
audio et l'absence de son dans les zones non sélectionnées, puis tester l'arrêt,
la fermeture du navigateur et un redémarrage de diffusion. Vérifier les priorités
avec les équipements déjà installés et la compatibilité des répéteurs.
Sur Windows, Compose sert à vérifier l'interface ; l'émission multicast depuis
le réseau Docker Desktop vers le LAN reste à valider. Pour la diffusion, privilégier
le déploiement Linux en réseau hôte.
