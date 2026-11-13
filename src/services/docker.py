import subprocess
import urllib3

from src.services import desktop_update

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DockerService:
    def __init__(self):
        self.kasm_url = "https://localhost:6901"

    def is_kasm_running(self):
        if not desktop_update.status(include_log=False)['ready']:
            return False
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=fernando-desktop", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            return "fernando-desktop" in result.stdout
        except:
            return False

    def get_kasm_url(self):
        return self.kasm_url

    def restart_kasm(self):
        return desktop_update.launch('restart')


docker_service = DockerService()
