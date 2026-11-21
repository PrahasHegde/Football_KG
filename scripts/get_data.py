"""Download (or update) the martj42/international_results dataset into data/."""
import subprocess
from pathlib import Path

dest = Path(__file__).resolve().parent.parent / "data" / "international_results"
url = "https://github.com/martj42/international_results.git"
if (dest / ".git").exists():
    subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=True)
else:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True)
print(f"Data ready in {dest}")
