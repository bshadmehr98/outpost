import flask
import subprocess

print(f"Hello from Outpost (flask {flask.__version__}, curl: {subprocess.run(['curl', '--version'], capture_output=True, text=True).stdout.split()[1]})")
