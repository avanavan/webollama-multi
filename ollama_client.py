import requests


class OllamaClient:
    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api"
        self.timeout = timeout

    def version(self):
        r = requests.get(f"{self.api}/version", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def tags(self):
        r = requests.get(f"{self.api}/tags", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def show(self, model):
        r = requests.post(f"{self.api}/show", json={"model": model}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def ps(self):
        r = requests.get(f"{self.api}/ps", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def pull(self, model, stream=False):
        return requests.post(
            f"{self.api}/pull", json={"model": model, "stream": stream}, stream=stream,
            timeout=(self.timeout, None),
        )

    def delete(self, model):
        return requests.delete(f"{self.api}/delete", json={"model": model}, timeout=self.timeout)

    def create(self, payload, stream=False):
        return requests.post(f"{self.api}/create", json=payload, stream=stream, timeout=(self.timeout, None))

    def generate(self, payload, stream=False):
        return requests.post(f"{self.api}/generate", json=payload, stream=stream, timeout=(self.timeout, None))

    def chat(self, payload, stream=True):
        return requests.post(f"{self.api}/chat", json=payload, stream=stream, timeout=(self.timeout, None))

    def ping(self):
        try:
            r = requests.get(f"{self.api}/version", timeout=2)
            return r.status_code == 200
        except (requests.RequestException, ConnectionError):
            return False
