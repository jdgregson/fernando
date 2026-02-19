import subprocess
import time
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class DockerService:
    def __init__(self):
        self.kasm_url = "https://localhost:6901"
    
    def is_kasm_running(self):
        try:
            result = subprocess.run(['docker', 'ps', '--filter', 'name=fernando-desktop', '--format', '{{.Names}}'],
                                  capture_output=True, text=True)
            return 'fernando-desktop' in result.stdout
        except:
            return False
    
    def start_kasm(self):
        if not self.is_kasm_running():
            subprocess.run(['docker-compose', 'up', '-d', 'fernando-desktop'], 
                         cwd='/home/coder/fernando')
        
        # Wait for service to be ready
        for i in range(60):
            try:
                resp = requests.get(self.kasm_url, timeout=2, verify=False, auth=('kasm_user', 'password'))
                if resp.status_code < 500:
                    return True
            except:
                pass
            time.sleep(1)
        return False
    
    def get_kasm_url(self):
        return self.kasm_url
    
    def restart_kasm(self):
        subprocess.run(['docker', 'restart', 'fernando-desktop'], check=True)
        time.sleep(2)
        return self.start_kasm()

docker_service = DockerService()
