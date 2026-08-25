# Backend Burkina Langues — Groq

## Configuration

1. Copiez `.env.example` vers `.env`.
2. Créez une clé dans la console Groq et renseignez `GROQ_API_KEY`.
3. Laissez `GROQ_MODEL=openai/gpt-oss-120b` pour privilégier la qualité.
4. Démarrez avec `python server.py`.

La clé Groq reste uniquement sur le serveur. Les applications web et mobile
utilisent le proxy backend et ne doivent jamais embarquer la clé dans leur code.

Langues de traduction : français, mooré, dioula et fulfuldé. Seuls le mooré,
le dioula et le fulfuldé sont des langues cibles locales exposées par l'API.

## Voix

Le backend Groq fournit la compréhension et la traduction. La reconnaissance
mobile actuelle utilise le moteur vocal de l'appareil, puis Groq corrige les
approximations à partir du dictionnaire et des règles d'Académie. Le TTS Groq
actuel ne propose officiellement que l'anglais et l'arabe : il ne faut donc pas
le présenter comme une voix native mooré/dioula/fulfuldé/gourounsi. Pour une voix
locale précise, utilisez des enregistrements validés par des locuteurs ou un
modèle TTS entraîné sur ces langues.
