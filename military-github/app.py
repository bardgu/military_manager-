"""Entry point for Hugging Face Spaces / Streamlit Cloud deployment."""
import sys
from pathlib import Path

# Add src to Python path so imports work
sys.path.insert(0, str(Path(__file__).parent / "src"))

from military_manager.main import main

main()
