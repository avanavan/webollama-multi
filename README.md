# WebOllama

A web interface for managing Ollama models and generating text using Python Flask and Bootstrap.

## Features

- View and manage local Ollama models
- Pull new models from the Ollama library
- Create custom models with system prompts and custom context size
- Chat with Ollama models with conversation history
- Generate text completions with customizable parameters
- View and unload models currently running in memory
- Monitor model resource usage and expiration
- Compare model versions and check for updates
- Display real-time changelog from GitHub releases
- Manage multiple Ollama servers from one panel; broadcast pull/delete/create with per-server progress; reconcile model drift; create models with custom context size and parameters
- Responsive UI with modern design for desktop and mobile

## Screenshots

### Home Page
![Home Page](assets/home.png)

### Models Page
![Models Page](assets/models.png)

### Version & Updates
![Version and Updates](assets/version-update.png)



## Installation

### Prerequisites

- Python 3.7 or higher
- [Ollama](https://ollama.ai/) installed and running

### Setup (Standard)

1. Clone this repository
```bash
git clone https://github.com/dkruyt/webollama.git
cd webollama
```

2. Run the setup script
```bash
./setup.sh
```

3. Or manually set up:
```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create a .env file (optional)
echo "SECRET_KEY=your-secret-key" > .env
echo "OLLAMA_API_BASE=http://localhost:11434" >> .env
```

### Setup (Docker)

The published image is **`ghcr.io/avanavan/webollama-multi:latest`** (multi-arch:
`linux/amd64` + `linux/arm64`). Both Compose files reference it; the managed
server list is persisted in a `webollama_data` volume mounted at `/data`.

#### Option 1: Connect to Ollama server(s) you already run

```bash
# Runs the WebOllama container only.
# Edit OLLAMA_API_BASE in docker-compose.yml to point at your first Ollama server,
# then add any others from the UI at /servers.
docker compose up -d
```

#### Option 2: Run WebOllama with TWO Ollama servers (multi-server demo)

```bash
# Brings up WebOllama + ollama1 (:11434) + ollama2 (:11435)
docker compose -f docker-compose.ollama.yml up -d
```

WebOllama seeds the first server (`ollama1`) automatically. Open
`http://127.0.0.1:5000/servers` and add the second:

| Name | Base URL |
|------|----------|
| Server 2 | `http://ollama2:11434` |

From then on, pulling/creating/deleting models broadcasts to both, with a
per-server progress bar each, and the Models page shows per-server presence with
one-click drift sync.

#### Container image & CI

Pushes to `main`, version tags (`v*`), and manual runs of the
**Docker Build and Push** GitHub Actions workflow
(`.github/workflows/docker-build.yml`) build the image and publish it to GHCR at
`ghcr.io/<owner>/<repo>` (e.g. `ghcr.io/avanavan/webollama-multi`). Pull
requests build the image but do not push. Run it directly with:

```bash
docker run -d -p 5000:5000 \
  -e OLLAMA_API_BASE=http://host.docker.internal:11434 \
  -v webollama_data:/data \
  ghcr.io/avanavan/webollama-multi:latest
```

## Usage

1. Make sure Ollama is running on your system

2. Start the web interface (if not using Docker)
```bash
python app.py
```

3. Open your browser and navigate to:
   - Standard install: `http://127.0.0.1:5000`
   - Docker: `http://127.0.0.1:5000`

## Features in Detail

### Model Management
- List, view details, and delete models
- Pull models from the Ollama library
- Create custom models with system prompts
- Customize model template and parameters
- Sort models by name, size, or modification date
- Monitor running models and resource usage
- Unload models from memory

### Generation & Chat
- Interactive chat interface with persistent conversation history
- Text generation with parameter customization
- Adjust temperature, top_p, top_k and other parameters
- Real-time streaming responses
- Preset parameters for different generation styles

### Version & Updates
- View current Ollama version
- Check for updates with real-time API calls
- View detailed changelog from GitHub releases
- Access download links for latest updates

## Configuration

The application can be configured using environment variables or a `.env` file:

- `SECRET_KEY`: Flask secret key for sessions (default: a development key)
- `OLLAMA_API_BASE`: Base URL of the Ollama API (default: `http://127.0.0.1:11434`). Only used to seed the first server on first run; after that, manage servers in the UI at `/servers`.
- `WEBOLLAMA_SERVERS_FILE`: Path to the JSON file storing managed servers (default: `servers.json` next to `app.py`)
- `PORT`: Port to run the web interface on (default: `5000`)
- `HOST`: Host to bind the web interface to (default: `127.0.0.1`)

If running with Docker, you can modify the ports and configuration in the Docker Compose files.

## Multiple Servers

WebOllama manages multiple Ollama servers from a single interface:

- **Manage servers in the UI** — add, edit, enable/disable, and remove servers at
  `/servers`; the list is persisted to `servers.json` (path set by
  `WEBOLLAMA_SERVERS_FILE`). `OLLAMA_API_BASE` only seeds the first server on first run.
- **Synced mutations** — pull, create, and delete broadcast to all enabled servers
  by default, with a per-action checkbox to narrow the targets.
- **Per-server progress** — pulling and creating show one live progress bar per
  target server.
- **Drift reconciliation** — the Models page merges every server's inventory, marks
  which models are missing where, and offers one-click **Sync** to pull a model to
  the servers that lack it.
- **Active server** — the navbar switcher picks the server used by single-server
  pages (Chat, Generate, Model detail, Version).

The fastest way to try it is `docker compose -f docker-compose.ollama.yml up -d`
(see [Option 2](#option-2-run-webollama-with-two-ollama-servers-multi-server-demo)).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT

## Acknowledgements

- [Ollama](https://ollama.ai/) for the amazing local LLM server
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Bootstrap](https://getbootstrap.com/) for the frontend components
